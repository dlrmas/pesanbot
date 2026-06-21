"""Penerimaan & pengiriman konten — satu mesin untuk teks/foto/video/audio/voice.

Dipakai oleh menfes, confes, balas anonim, broadcast, preview, dan review admin.
"""
from app.core import utils

TYPES = ("text", "photo", "video", "animation", "audio", "voice", "document")

_LABEL = {
    "text": "📝 Teks",
    "photo": "📷 Foto",
    "video": "🎬 Video",
    "animation": "🎞️ GIF",
    "audio": "🎵 Audio",
    "voice": "🎙️ Voice note",
    "document": "📎 File",
}


def extract(m) -> dict | None:
    """Ambil konten terdukung dari pesan Telegram; None bila tidak didukung."""
    if m.text:
        return {"type": "text", "text": m.text}
    if m.photo:
        return {"type": "photo", "file_id": m.photo.file_id, "caption": m.caption or ""}
    if m.video:
        return {"type": "video", "file_id": m.video.file_id, "caption": m.caption or ""}
    if m.animation:  # GIF — diperiksa sebelum document (Telegram menandai GIF sbg animation)
        return {"type": "animation", "file_id": m.animation.file_id, "caption": m.caption or ""}
    if m.audio:
        return {"type": "audio", "file_id": m.audio.file_id, "caption": m.caption or ""}
    if m.voice:
        return {"type": "voice", "file_id": m.voice.file_id, "caption": m.caption or ""}
    if m.document:
        return {"type": "document", "file_id": m.document.file_id, "caption": m.caption or ""}
    return None


def label(c: dict) -> str:
    return _LABEL.get(c.get("type"), "❓")


def body(c: dict) -> str:
    """Teks yang dimoderasi: isi teks atau caption media."""
    return c.get("text") or c.get("caption") or ""


def check_limits(c: dict, s: dict) -> str | None:
    if c["type"] == "text":
        if not c["text"].strip():
            return "Pesan kosong."
        if len(c["text"]) > s["max_text"]:
            return f"Teks terlalu panjang (maks {s['max_text']} karakter)."
    elif len(c.get("caption", "")) > s["max_caption"]:
        return f"Caption terlalu panjang (maks {s['max_caption']} karakter)."
    return None


async def send(client, chat_id, c: dict, extra: str = None, kb=None, effect: int = None,
               head: str = None, spoiler: bool = False):
    """Kirim konten apa adanya: `head` (HTML) di atas, blok `extra` (HTML) di bawah.

    Isi/caption pengguna selalu di-escape; `head`/`extra` dianggap HTML tepercaya.
    `spoiler=True` → teks dibungkus <spoiler> dan foto/video diburamkan
    (has_spoiler) sampai diketuk — momen "buka pesan rahasia".
    Bila efek ditolak server, otomatis kirim ulang tanpa efek.
    """
    prefix = f"{head}\n\n" if head else ""
    suffix = f"\n\n{extra}" if extra else ""
    pad = len(prefix) + len(suffix) + 24
    if c["type"] == "text":
        body = utils.take(utils.esc(c["text"]), 4096 - pad)
        if spoiler:
            body = f"<spoiler>{body}</spoiler>"
        return await _try_effect(
            client.send_message, effect,
            chat_id=chat_id, text=prefix + body + suffix, parse_mode=utils.HTML,
            link_preview_options=utils.NOPREV, reply_markup=kb,
        )
    body = utils.take(utils.esc(c.get("caption", "")), 1024 - pad)
    if spoiler and body:
        body = f"<spoiler>{body}</spoiler>"
    caption = (prefix + body + suffix).strip() or None
    fn = {
        "photo": client.send_photo,
        "video": client.send_video,
        "animation": client.send_animation,
        "audio": client.send_audio,
        "voice": client.send_voice,
        "document": client.send_document,
    }[c["type"]]
    kwargs = dict(
        chat_id=chat_id, **{c["type"]: c["file_id"]},
        caption=caption, parse_mode=utils.HTML, reply_markup=kb,
    )
    # has_spoiler hanya didukung media visual; document/audio/voice tidak.
    if spoiler and c["type"] in ("photo", "video", "animation"):
        kwargs["has_spoiler"] = True
    return await _try_effect(fn, effect, **kwargs)


async def _try_effect(fn, effect, **kwargs):
    if effect:
        try:
            return await fn(effect_id=effect, **kwargs)
        except Exception:
            pass
    return await fn(**kwargs)
