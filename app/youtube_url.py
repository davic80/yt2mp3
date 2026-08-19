"""youtube_url.py — parsing and normalization of YouTube URLs.

Kept dependency-free (stdlib only) so it can be unit-tested without booting
the Flask app.

The public helpers are:
  ``is_youtube_url``   — accept/reject a pasted URL
  ``extract_video_id`` — pull the 11-char video ID out of any accepted form
  ``canonical_url``    — rewrite to https://www.youtube.com/watch?v=<id>
  ``is_bare_playlist`` — True only for playlist URLs with no video in them
"""
import re
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

# Hosts YouTube itself hands out.  ``youtu.be`` is the official short-link
# domain used by the mobile share sheet, so it must be treated as first class.
_SHORT_HOSTS = {"youtu.be", "www.youtu.be"}
_LONG_HOSTS = {
    "youtube.com", "www.youtube.com",
    "m.youtube.com", "music.youtube.com",
}

# Path prefixes on youtube.com that carry the video ID as the next segment.
_ID_PATH_PREFIXES = ("shorts", "embed", "live", "v")

YOUTUBE_RE = re.compile(
    r"^(https?://)?"
    r"((www|m|music)\.)?"
    r"(youtube\.com/(watch\?|playlist\?|shorts/|embed/|live/|v/)|youtu\.be/)"
    r"[\w\-?=&%.]+"
)


def is_youtube_url(url: str) -> bool:
    """Return True if *url* looks like a YouTube video or playlist link."""
    return bool(YOUTUBE_RE.match((url or "").strip()))


def _parsed(url: str):
    """urlparse *url*, tolerating a missing scheme (``youtu.be/xyz``)."""
    candidate = (url or "").strip()
    if not re.match(r"^https?://", candidate, flags=re.IGNORECASE):
        candidate = "https://" + candidate
    return urlparse(candidate)


def extract_video_id(url: str) -> str | None:
    """Return the YouTube video ID from a URL, or None if not parseable.

    Handles every form YouTube hands out:
      https://www.youtube.com/watch?v=XXXXXXXXXXX
      https://youtu.be/XXXXXXXXXXX            (optionally with ?si=/?is= noise)
      https://www.youtube.com/shorts/XXXXXXXXXXX
      https://www.youtube.com/embed/XXXXXXXXXXX
      https://www.youtube.com/live/XXXXXXXXXXX
    """
    try:
        parsed = _parsed(url)
        host = parsed.netloc.lower()
        parts = [p for p in parsed.path.split("/") if p]

        # youtu.be/<id> — the ID is the first path segment, everything after
        # the '?' is share tracking noise (si=, is=, feature=, ...).
        if host in _SHORT_HOSTS:
            return parts[0] if parts else None

        if host not in _LONG_HOSTS:
            return None

        # youtube.com/watch?v=<id>
        params = parse_qs(parsed.query)
        if params.get("v") and params["v"][0]:
            return params["v"][0]

        # youtube.com/shorts|embed|live|v/<id>
        if len(parts) >= 2 and parts[0] in _ID_PATH_PREFIXES:
            return parts[1] or None
    except Exception:
        pass
    return None


def is_bare_playlist(url: str) -> bool:
    """Return True if the URL points at a playlist and at no specific video.

    ``youtu.be/<id>?list=PL...`` and ``watch?v=<id>&list=PL...`` both name a
    video, so they are *not* bare — they download that single track.
    """
    try:
        parsed = _parsed(url)
        if "list" not in parse_qs(parsed.query):
            return False
        return extract_video_id(url) is None
    except Exception:
        return False


def canonical_url(url: str) -> str:
    """Normalize *url* to the plain single-video form yt-dlp handles best.

    Any URL naming a video becomes ``https://www.youtube.com/watch?v=<id>``,
    which drops playlist params (``list``, ``index``, ``start_radio``) and the
    share tracking params ``youtu.be`` links carry (``si``, ``is``, ...).
    A ``t=`` start offset is preserved when present.

    Bare playlist URLs are returned unchanged — the caller decides whether to
    treat them as a batch or let yt-dlp grab the first track.
    """
    video_id = extract_video_id(url)
    if not video_id:
        return url

    query = {"v": video_id}
    try:
        params = parse_qs(_parsed(url).query)
        if params.get("t") and params["t"][0]:
            query["t"] = params["t"][0]
    except Exception:
        pass

    return urlunparse(("https", "www.youtube.com", "/watch", "", urlencode(query), ""))


__all__ = [
    "YOUTUBE_RE",
    "is_youtube_url",
    "extract_video_id",
    "is_bare_playlist",
    "canonical_url",
]
