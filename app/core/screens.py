"""Clean Chat UI — satu pesan bot aktif per chat sebagai layar utama.

Utamakan edit; bila jenis konten berubah / butuh efek, hapus lalu kirim baru.
Kegagalan edit/hapus tidak boleh menggagalkan alur.
"""
import logging

from pyrogram.errors import MessageNotModified

from app import db
from app.core import content as content_mod
from app.core import effects, ui, utils

log = logging.getLogger("screens")

# Layar lebih tua dari ini tidak diedit, tapi dikirim ulang. Penting: bila
# pengguna menghapus chat (sisi dia saja), pesan lama masih "hidup" di sisi
# bot — edit akan sukses tapi tak terlihat pengguna (layar hantu).
EDIT_MAX_AGE = 1800  # detik


def _fresh(rec) -> bool:
    at = utils.aware(rec.get("at")) if rec else None
    return bool(at and (utils.now() - at).total_seconds() < EDIT_MAX_AGE)


async def show(client, chat_id: int, text: str, kb=None, *, effect: str = None, content: dict = None):
    """Tampilkan layar. `effect` = nama event semantik (lihat core.effects).

    Bila layar sebelumnya adalah prompt input (reply keyboard kontekstual),
    keyboard itu ditutup otomatis di sini — layar pengguna kembali bersih.
    """
    eff = effects.effect_for(effect)
    rec = await db.screens.find_one({"_id": chat_id})
    mid = rec.get("mid") if rec else None
    had_input_kb = bool(rec and rec.get("kb"))

    if mid and _fresh(rec) and not had_input_kb and not eff and not content:
        try:
            await client.edit_message_text(
                chat_id, mid, text,
                parse_mode=utils.HTML, link_preview_options=utils.NOPREV, reply_markup=kb,
            )
            return
        except MessageNotModified:
            return
        except Exception:
            pass

    if mid:
        try:
            await client.delete_messages(chat_id, mid)
        except Exception:
            pass

    if content:
        try:
            m = await content_mod.send(client, chat_id, content, extra=text, kb=kb, effect=eff)
        except Exception as e:  # media gagal (file_id basi/korup) → jangan rusak layar
            log.warning("kirim konten gagal (%s) — fallback ke teks", type(e).__name__)
            m = await _send_text(client, chat_id, text, kb, eff)
    else:
        m = await _send_text(client, chat_id, text, kb, eff)
    await db.screens.update_one(
        {"_id": chat_id},
        {"$set": {"mid": m.id, "at": utils.now(), "kb": False}},
        upsert=True,
    )
    if had_input_kb:
        await set_reply_kb(client, chat_id, ui.remove_keyboard())
    return m


async def _send_text(client, chat_id, text, kb, eff):
    if eff:
        try:
            return await client.send_message(
                chat_id, text, parse_mode=utils.HTML,
                link_preview_options=utils.NOPREV, reply_markup=kb, effect_id=eff,
            )
        except Exception:
            pass
    return await client.send_message(
        chat_id, text, parse_mode=utils.HTML,
        link_preview_options=utils.NOPREV, reply_markup=kb,
    )


async def prompt(client, chat_id: int, text: str, markup):
    """Ganti layar aktif dengan prompt input ber-reply-keyboard kontekstual
    (tidak bisa lewat edit). Ditandai `kb` agar show() menutupnya otomatis."""
    rec = await db.screens.find_one({"_id": chat_id})
    if rec and rec.get("mid"):
        try:
            await client.delete_messages(chat_id, rec["mid"])
        except Exception:
            pass
    m = await client.send_message(
        chat_id, text, parse_mode=utils.HTML, link_preview_options=utils.NOPREV,
        reply_markup=markup,
    )
    await db.screens.update_one(
        {"_id": chat_id}, {"$set": {"mid": m.id, "at": utils.now(), "kb": True}}, upsert=True
    )
    return m


async def reset(client, chat_id: int):
    """Lupakan (dan coba hapus) layar aktif — layar berikutnya pasti pesan baru.

    Wajib saat /start: menyembuhkan 'layar hantu' setelah pengguna menghapus chat.
    """
    rec = await db.screens.find_one({"_id": chat_id})
    if rec and rec.get("mid"):
        try:
            await client.delete_messages(chat_id, rec["mid"])
        except Exception:
            pass
    await db.screens.delete_one({"_id": chat_id})
    if rec and rec.get("kb"):
        await set_reply_kb(client, chat_id, ui.remove_keyboard())


async def ask(client, chat_id: int, text: str, placeholder: str = None, choices: list = None):
    """Prompt input kontekstual: keyboard berisi pilihan cepat (bila ada) +
    tombol 🗑 Batal yang SELALU ada — membatalkan cukup satu ketukan, tanpa
    perlu mengetik perintah. Keyboard menutup sendiri usai dipakai."""
    return await prompt(client, chat_id, text, ui.input_keyboard(choices, placeholder))


async def set_reply_kb(client, chat_id: int, kb, note: str = None):
    """Pasang/ganti reply keyboard. Tanpa `note`, pesan pembawa dihapus senyap
    (keyboard tetap aktif di chat) — best effort, tak mengganggu alur."""
    try:
        m = await client.send_message(
            chat_id, note or "⌨️", parse_mode=utils.HTML, reply_markup=kb,
        )
        if not note:
            await m.delete()
        return m
    except Exception:
        return None


async def drop(client, m) -> bool:
    """Hapus pesan input pengguna setelah tersimpan (jika Telegram mengizinkan).

    -> True bila benar-benar terhapus. Pemanggil yang menangani konten sensitif
    (mis. token) wajib memeriksa nilai ini dan memperingatkan bila gagal.
    """
    try:
        await m.delete()
        return True
    except Exception:
        return False


async def typing(client, chat_id: int):
    """Indikator 'mengetik…' sekejap sebelum aksi yang terasa berat — best effort."""
    try:
        from pyrogram import enums as _enums
        await client.send_chat_action(chat_id, _enums.ChatAction.TYPING)
    except Exception:
        pass


async def notify(client, chat_id: int, text: str, kb=None, effect: str = None):
    """Notifikasi peristiwa (di luar layar utama) — best effort, tak mengganggu alur."""
    eff = effects.effect_for(effect)
    try:
        if eff:
            try:
                return await client.send_message(
                    chat_id, text, parse_mode=utils.HTML,
                    link_preview_options=utils.NOPREV, reply_markup=kb, effect_id=eff,
                )
            except Exception:
                pass
        return await client.send_message(
            chat_id, text, parse_mode=utils.HTML,
            link_preview_options=utils.NOPREV, reply_markup=kb,
        )
    except Exception:
        log.debug("notify gagal ke %s", chat_id)
        return None
