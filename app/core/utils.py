"""Utilitas bersama: waktu WIB, escape HTML, format, kode acak."""
import html
import secrets
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from bson import ObjectId
from pyrogram import enums
from pyrogram.types import LinkPreviewOptions

WIB = ZoneInfo("Asia/Jakarta")
HTML = enums.ParseMode.HTML
NOPREV = LinkPreviewOptions(is_disabled=True)

_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # tanpa karakter membingungkan


def now() -> datetime:
    return datetime.now(timezone.utc)


def now_wib() -> datetime:
    return datetime.now(WIB)


def today() -> str:
    """Tanggal hari ini dalam WIB — kunci reset poin harian (00:00 WIB, lazy)."""
    return now_wib().strftime("%Y-%m-%d")


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def aware(dt):
    """MongoDB kadang mengembalikan datetime naive; pastikan UTC-aware."""
    if dt and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def fmt_dt(dt) -> str:
    dt = aware(dt)
    return dt.astimezone(WIB).strftime("%d %b %Y %H:%M WIB") if dt else "-"


def left_str(seconds: int) -> str:
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    parts = []
    if h:
        parts.append(f"{h} jam")
    if m:
        parts.append(f"{m} mnt")
    if s and not h:
        parts.append(f"{s} dtk")
    return " ".join(parts) or "0 dtk"


def take(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


def gen_code(n: int = 8) -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(n))


def gen_token(n: int = 10) -> str:
    return secrets.token_hex(n // 2 + 1)[:n]


def oid(s):
    try:
        return ObjectId(str(s))
    except Exception:
        return None


def parse_int(s):
    try:
        return int(str(s).strip())
    except (TypeError, ValueError):
        return None
