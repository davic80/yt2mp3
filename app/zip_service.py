"""zip_service.py — building ZIP downloads without holding them in memory.

All three ZIP endpoints used to assemble the archive in a ``BytesIO``, and the
playlist one then called ``getvalue()`` on top, which copies the whole thing a
second time. Fifty tracks is roughly half a gigabyte of peak RSS that way — on
a Raspberry Pi that is an OOM waiting to happen, and it was the reason the
playlist ZIP was capped at half the tracks a playlist may hold.

The archive is written to a temporary file instead. The file is unlinked as
soon as it is open, so the bytes live only as long as the file descriptor: the
kernel frees them when the response finishes, and also if the worker dies
mid-transfer. Peak memory is one zipfile buffer regardless of archive size.
"""
import logging
import os
import tempfile
import zipfile

from flask import send_file

logger = logging.getLogger("app")


def unique_arcname(name, seen):
    """Return a ZIP entry name that is safe and not already used.

    *seen* is a dict carried across calls; it is mutated.
    """
    name = (name or "track.mp3").replace("\\", "/")
    name = os.path.basename(name).strip()
    if not name:
        name = "track.mp3"
    if not name.lower().endswith(".mp3"):
        name += ".mp3"

    if name not in seen:
        seen[name] = 0
        return name

    seen[name] += 1
    stem, ext = os.path.splitext(name)
    return f"{stem} ({seen[name]}){ext}"


def send_zip(entries, download_name):
    """Stream a ZIP of *entries* — an iterable of ``(source_path, arcname)``.

    Entries whose source file is missing are skipped. Returns ``None`` if
    nothing could be added, so the caller can answer 404.

    Stored uncompressed: MP3 is already compressed, so deflating it burns CPU
    on a Pi for a percent or two of size.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    try:
        # Unlink now: the descriptor keeps the data alive, and nothing is left
        # behind if this worker dies before the response completes.
        os.unlink(tmp.name)
    except OSError:
        pass

    written = 0
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as archive:
            for source_path, arcname in entries:
                if not source_path or not os.path.isfile(source_path):
                    continue
                archive.write(source_path, arcname)
                written += 1
    except Exception:
        tmp.close()
        raise

    if not written:
        tmp.close()
        return None

    tmp.seek(0)
    logger.info("zip: streaming %s (%d entries)", download_name, written)

    return send_file(
        tmp,
        mimetype="application/zip",
        as_attachment=True,
        download_name=download_name,
    )
