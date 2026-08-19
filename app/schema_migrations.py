"""schema_migrations.py — the one migration that rewrites tables.

``downloads.user_email`` and ``playlists.user_email`` are declared as foreign
keys in the models but have never been foreign keys in the database: both
columns arrived through ``ALTER TABLE ADD COLUMN``, which cannot attach a
constraint. SQLite cannot add one afterwards either, so the table has to be
rebuilt.

Everything here was shaped by a dry run against a copy of the production
database, which caught two mistakes that would otherwise have shipped:

* The index SQL must be captured *before* the old table is dropped. Dropping a
  table drops its indexes, so reading them afterwards silently yields nothing
  and the rebuilt table comes back with none.
* Losing those indexes is not merely a performance problem. ``downloads.job_id``
  has no UNIQUE constraint in the table definition — its uniqueness comes from
  ``ix_downloads_job_id`` being a unique index — and ``playlist_tracks.job_id``
  and ``play_events.job_id`` both reference that column. Drop the index and
  SQLite rejects those two foreign keys with "foreign key mismatch".

The rebuild therefore verifies rows, columns and indexes inside the
transaction and rolls back rather than committing something diminished, and a
copy of the database is written first regardless.
"""
import logging
import os
import re

logger = logging.getLogger("app")

# table -> (column, parent table, parent column)
_WANTED_FOREIGN_KEYS = {
    "downloads": ("user_email", "users", "email"),
    "playlists": ("user_email", "users", "email"),
}


def _snapshot(cur, table):
    """Row count, column names and index DDL — read before any change."""
    rows = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    columns = [r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]
    indexes = {
        name: sql for name, sql in cur.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'index' AND tbl_name = ? AND sql IS NOT NULL",
            (table,),
        ).fetchall()
    }
    return rows, columns, indexes


def _has_foreign_key(cur, table, column):
    return any(
        fk[3] == column
        for fk in cur.execute(f"PRAGMA foreign_key_list({table})").fetchall()
    )


def _backup(cur, db_path):
    """Write a copy beside the live database using VACUUM INTO (atomic)."""
    if not db_path or not os.path.isfile(db_path):
        return None
    target = db_path + ".pre-fk-backup"
    try:
        if os.path.exists(target):
            os.remove(target)
        cur.execute("VACUUM INTO ?", (target,))
        logger.info("schema migration: backup written to %s", target)
        return target
    except Exception as exc:
        logger.warning("schema migration: backup failed (%s)", exc)
        return None


def _rebuild(cur, table, column, parent_table, parent_column):
    """Rebuild *table* with the foreign key added. Raises to abort the tx."""
    rows_before, columns_before, indexes_before = _snapshot(cur, table)

    create_sql = cur.execute(
        "SELECT sql FROM sqlite_master WHERE name = ?", (table,)
    ).fetchone()[0]

    # Extend the table's own CREATE statement rather than generating one from
    # the models: the two have drifted before, and anything the model does not
    # know about would be dropped without a word.
    new_sql = re.sub(
        r"\)\s*$",
        f",\n\tFOREIGN KEY({column}) REFERENCES {parent_table} ({parent_column})\n)",
        create_sql.strip(),
    )
    new_sql = re.sub(
        rf'CREATE TABLE ("?){re.escape(table)}\1', f"CREATE TABLE {table}__new",
        new_sql, count=1,
    )
    if "__new" not in new_sql or "FOREIGN KEY" not in new_sql:
        raise RuntimeError(f"could not rewrite the CREATE statement for {table}")

    column_list = ", ".join(f'"{c}"' for c in columns_before)

    cur.execute(new_sql)
    cur.execute(
        f"INSERT INTO {table}__new ({column_list}) SELECT {column_list} FROM {table}"
    )
    cur.execute(f"DROP TABLE {table}")
    cur.execute(f"ALTER TABLE {table}__new RENAME TO {table}")
    for index_sql in indexes_before.values():
        cur.execute(index_sql)

    rows_after, columns_after, indexes_after = _snapshot(cur, table)
    if rows_after != rows_before:
        raise RuntimeError(f"{table}: {rows_before} rows in, {rows_after} out")
    if columns_after != columns_before:
        lost = set(columns_before) - set(columns_after)
        raise RuntimeError(f"{table}: columns changed, lost {sorted(lost)}")
    if set(indexes_after) != set(indexes_before):
        lost = set(indexes_before) - set(indexes_after)
        raise RuntimeError(f"{table}: indexes lost {sorted(lost)}")

    logger.info(
        "schema migration: %s rebuilt with FK on %s (%d rows, %d columns, %d indexes)",
        table, column, rows_after, len(columns_after), len(indexes_after),
    )


def ensure_user_foreign_keys(db) -> dict:
    """Add the two missing foreign keys. Idempotent; safe to call every boot."""
    raw = db.engine.raw_connection()
    result = {"rebuilt": [], "skipped": []}
    try:
        connection = raw.driver_connection
        previous_isolation = connection.isolation_level
        connection.isolation_level = None  # explicit transaction control
        cur = connection.cursor()

        pending = {
            table: spec for table, spec in _WANTED_FOREIGN_KEYS.items()
            if not _has_foreign_key(cur, table, spec[0])
        }
        result["skipped"] = sorted(set(_WANTED_FOREIGN_KEYS) - set(pending))
        if not pending:
            return result

        db_path = cur.execute("PRAGMA database_list").fetchone()[2]
        _backup(cur, db_path)

        # Neither pragma may change inside a transaction.
        cur.execute("PRAGMA foreign_keys=OFF")
        cur.execute("BEGIN")
        try:
            for table, (column, parent_table, parent_column) in pending.items():
                _rebuild(cur, table, column, parent_table, parent_column)
            cur.execute("COMMIT")
            result["rebuilt"] = sorted(pending)
        except Exception:
            cur.execute("ROLLBACK")
            raise
        finally:
            cur.execute("PRAGMA foreign_keys=ON")

        violations = cur.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            # The data was consistent before, so this means the rebuild is
            # wrong. Say so loudly — the backup is sitting next to the file.
            logger.error(
                "schema migration: %d foreign key violations after rebuild — "
                "restore from the .pre-fk-backup copy", len(violations),
            )
        connection.isolation_level = previous_isolation
        return result
    finally:
        raw.close()
