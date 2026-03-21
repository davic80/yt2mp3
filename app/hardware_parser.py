"""
hardware_parser.py — Infer hardware model and compute a stable identity hash
from browser fingerprint components.

detect_hardware(fp_json_str) -> str
    Returns a human-readable string like:
        "Apple M1 Pro · MacBook Pro"
        "iPhone 15 (iOS 18.7)"
        "iPad Pro 12.9\" (iPadOS 17.4)"
        "NVIDIA GeForce RTX 3080"
        "Android · Samsung Galaxy S21 Ultra"
        "—"

compute_identity_hash(fp_json_str) -> str
    Returns an 8-char hex hash stable across browser sessions on the same
    physical device. Uses hardware-stable fields only (webGL, platform,
    hardwareConcurrency, deviceMemory, timezone, maxTouchPoints, canvas[:64]).

    NOTE: Safari and Chrome on the same Mac may produce different hashes
    because webGL and canvas rendering differ between browser engines.
"""

import hashlib
import json
import re


# ── iPhone resolution → model mapping ────────────────────────────────────────
# Key: (width, height) normalised to portrait (min x max), iOS major version
# Value: (model_name, min_ios) — pick the entry whose min_ios ≤ actual iOS

_IPHONE_SCREENS = [
    # width, height, min_ios, model
    (430, 932, 17, "iPhone 15 Plus"),
    (430, 932, 16, "iPhone 14 Plus"),
    (393, 852, 17, "iPhone 15"),
    (393, 852, 16, "iPhone 14"),
    (390, 844, 15, "iPhone 13"),
    (390, 844, 14, "iPhone 12"),
    (428, 926, 15, "iPhone 13 Pro Max"),
    (428, 926, 14, "iPhone 12 Pro Max"),
    (414, 896, 13, "iPhone 11"),
    (414, 896, 12, "iPhone XR / XS Max"),
    (375, 812, 13, "iPhone 11 Pro"),
    (375, 812, 12, "iPhone X / XS"),
    (414, 736, 0,  "iPhone 8 Plus"),
    (375, 667, 0,  "iPhone SE / 8"),
    (320, 568, 0,  "iPhone SE (1st gen)"),
]

# ── iPad resolution → model mapping ──────────────────────────────────────────
_IPAD_SCREENS = [
    (1024, 1366, 16, "iPad Pro 12.9\""),
    (1024, 1366, 0,  "iPad Pro 12.9\""),
    (834, 1194, 16, "iPad Pro 11\""),
    (834, 1194, 0,  "iPad Pro 11\""),
    (820, 1180, 0,  "iPad Air 10.9\""),
    (810, 1080, 0,  "iPad 10.2\""),
    (768, 1024, 0,  "iPad mini / Air"),
]

# ── Common Android models from UA snippet ────────────────────────────────────
_ANDROID_MODELS = [
    # (ua_substring, display_name)
    ("SM-S9",  "Samsung Galaxy S23"),
    ("SM-S90", "Samsung Galaxy S23"),
    ("SM-S91", "Samsung Galaxy S23"),
    ("SM-G99", "Samsung Galaxy S21"),
    ("SM-G99", "Samsung Galaxy S21"),
    ("SM-G97", "Samsung Galaxy S10"),
    ("SM-A54", "Samsung Galaxy A54"),
    ("SM-A53", "Samsung Galaxy A53"),
    ("Pixel 8 Pro", "Google Pixel 8 Pro"),
    ("Pixel 8",     "Google Pixel 8"),
    ("Pixel 7 Pro", "Google Pixel 7 Pro"),
    ("Pixel 7",     "Google Pixel 7"),
    ("Pixel 6",     "Google Pixel 6"),
    ("OnePlus",     "OnePlus"),
    ("ONEPLUS",     "OnePlus"),
    ("Mi ",         "Xiaomi"),
    ("Redmi",       "Xiaomi Redmi"),
]

# ── Apple chip → most likely Mac model ───────────────────────────────────────
# M1/M2/M3 Pro/Max are only in MacBook Pro / Mac Studio / Mac Pro
# M1/M2/M3 base + Air are in MacBook Air / Mac mini
_CHIP_TO_MAC = {
    "M1 Pro":  "MacBook Pro",
    "M1 Max":  "MacBook Pro / Mac Studio",
    "M1 Ultra":"Mac Studio / Mac Pro",
    "M1":      "MacBook Air / Mac mini",
    "M2 Pro":  "MacBook Pro / Mac mini",
    "M2 Max":  "MacBook Pro / Mac Studio",
    "M2 Ultra":"Mac Studio / Mac Pro",
    "M2":      "MacBook Air / Mac mini",
    "M3 Pro":  "MacBook Pro",
    "M3 Max":  "MacBook Pro",
    "M3":      "MacBook Air / MacBook Pro",
    "M4 Pro":  "MacBook Pro / Mac mini",
    "M4 Max":  "MacBook Pro",
    "M4":      "MacBook Air / Mac mini",
}


def _parse_fp(fp_json_str: str) -> dict:
    if not fp_json_str:
        return {}
    try:
        return json.loads(fp_json_str)
    except Exception:
        return {}


def _screen_portrait(fp: dict):
    """Return (w, h) in portrait orientation (smaller, larger)."""
    raw = fp.get("screen", "")
    parts = raw.split("x")
    if len(parts) < 2:
        return None, None
    try:
        w, h = int(parts[0]), int(parts[1])
        return min(w, h), max(w, h)
    except ValueError:
        return None, None


def _ios_version(ua: str):
    """Extract (major, minor) iOS version from a UA string, or (0, 0)."""
    m = re.search(r"CPU (?:iPhone )?OS (\d+)_(\d+)", ua)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"OS (\d+)_(\d+)", ua)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 0, 0


def _detect_iphone(fp: dict, ua: str) -> str:
    major, minor = _ios_version(ua)
    w, h = _screen_portrait(fp)
    model = None
    if w and h:
        # Walk table: pick best match (highest min_ios that is still ≤ actual)
        best_min_ios = -1
        for sw, sh, min_ios, name in _IPHONE_SCREENS:
            if sw == w and sh == h and min_ios <= major and min_ios > best_min_ios:
                best_min_ios = min_ios
                model = name
    if model:
        return f"{model} (iOS {major}.{minor})"
    return f"iPhone (iOS {major}.{minor})" if major else "iPhone"


def _detect_ipad(fp: dict, ua: str) -> str:
    major, minor = _ios_version(ua)
    w, h = _screen_portrait(fp)
    model = None
    if w and h:
        best_min_ios = -1
        for sw, sh, min_ios, name in _IPAD_SCREENS:
            if sw == w and sh == h and min_ios <= major and min_ios > best_min_ios:
                best_min_ios = min_ios
                model = name
    os_label = f"iPadOS {major}.{minor}" if major else "iPadOS"
    if model:
        return f"{model} ({os_label})"
    return f"iPad ({os_label})"


def _detect_android(ua: str) -> str:
    for snippet, name in _ANDROID_MODELS:
        if snippet in ua:
            return f"Android · {name}"
    # Generic: try to extract model from UA
    m = re.search(r"Android [^;]+; ([^)]+)\)", ua)
    if m:
        raw_model = m.group(1).strip()
        if raw_model and raw_model.lower() not in ("mobile", "tablet"):
            return f"Android · {raw_model[:40]}"
    return "Android"


def detect_hardware(fp_json_str: str) -> str:
    """
    Infer hardware model from fingerprint components JSON string.
    Returns a display string or '—'.
    """
    fp = _parse_fp(fp_json_str)
    if not fp:
        return "—"

    ua = fp.get("ua", "")
    platform = fp.get("platform", "")
    webgl = fp.get("webGL", "")

    # ── 1. Apple Silicon / AMD / NVIDIA from webGL ────────────────────────────

    # Apple Silicon chip (Chrome/Firefox on Mac)
    m = re.search(
        r"ANGLE\s*\(Apple,\s*ANGLE Metal Renderer:\s*Apple\s+(M\d+(?:\s+(?:Pro|Max|Ultra))?)",
        webgl,
        re.IGNORECASE,
    )
    if m:
        chip = "Apple " + m.group(1).strip()
        chip_short = m.group(1).strip()  # e.g. "M1 Pro"
        mac_model = _CHIP_TO_MAC.get(chip_short, "Mac")
        return f"{chip} · {mac_model}"

    # NVIDIA (Windows/Linux, ANGLE)
    m = re.search(r"ANGLE.*?NVIDIA[, ]+(.+?)(?:,|\))", webgl, re.IGNORECASE)
    if m:
        gpu = m.group(1).strip()
        return f"NVIDIA {gpu}"

    # AMD (Windows, ANGLE)
    m = re.search(r"ANGLE.*?AMD\s+(.+?)(?:,|\))", webgl, re.IGNORECASE)
    if m:
        gpu = m.group(1).strip()
        return f"AMD {gpu}"

    # Intel (Windows, ANGLE)
    m = re.search(r"ANGLE.*?Intel[® ]+(.+?)(?:,|\))", webgl, re.IGNORECASE)
    if m:
        gpu = m.group(1).strip()
        return f"Intel {gpu}"

    # Direct NVIDIA/AMD strings (non-ANGLE)
    m = re.search(r"(NVIDIA GeForce .+?)(?:\s*$|,)", webgl)
    if m:
        return m.group(1).strip()
    m = re.search(r"(Radeon .+?)(?:\s*$|,)", webgl, re.IGNORECASE)
    if m:
        return "AMD " + m.group(1).strip()

    # ── 2. Mobile Apple (webGL = "Apple GPU") ────────────────────────────────

    if platform == "iPhone" or "iPhone" in ua:
        return _detect_iphone(fp, ua)

    if platform == "iPad" or "iPad" in ua:
        return _detect_ipad(fp, ua)

    # Mac with "Apple GPU" but no chip detail (Safari on Apple Silicon)
    if platform == "MacIntel" and webgl in ("Apple GPU", ""):
        return "Mac · Apple Silicon (Safari)"

    # ── 3. Android ────────────────────────────────────────────────────────────

    if "Android" in ua:
        return _detect_android(ua)

    # ── 4. Generic fallbacks ──────────────────────────────────────────────────

    if "Windows" in ua or platform.startswith("Win"):
        return "Windows PC"

    if "Linux" in ua and "Android" not in ua:
        return "Linux"

    if platform == "MacIntel":
        return "Mac"

    return "—"


def compute_identity_hash(fp_json_str: str) -> str:
    """
    Compute an 8-char hex hash from hardware-stable fingerprint fields.
    Stable across browser sessions; may differ between Chrome and Safari
    on the same Mac due to canvas/webGL rendering differences.
    """
    fp = _parse_fp(fp_json_str)
    if not fp:
        return "—"

    stable = {
        "platform":            fp.get("platform"),
        "webGL":               fp.get("webGL"),
        "hardwareConcurrency": fp.get("hardwareConcurrency"),
        "deviceMemory":        fp.get("deviceMemory"),
        "timezone":            fp.get("timezone"),
        "maxTouchPoints":      fp.get("maxTouchPoints"),
        "canvas":              (fp.get("canvas") or "")[:64],
    }
    raw = json.dumps(stable, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
