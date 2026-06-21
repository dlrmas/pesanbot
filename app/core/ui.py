"""Bahasa visual bersama — satu sumber gaya untuk semua layar (sarang laba-laba).

Memakai fitur Telegram modern (terverifikasi di Kurigram 2.2.23):
- tombol berwarna (ButtonStyle PRIMARY / SUCCESS / DANGER);
- tombol salin sekali-ketuk (copy_text);
- blockquote & spoiler HTML untuk hierarki visual.

Konsep keyboard (dipakai konsisten lewat screens.ask / screens.show):
- InlineKeyboardMarkup → navigasi & aksi pada layar utama;
- ReplyKeyboardMarkup → SEMUA prompt input; SELALU membawa tombol 🗑 Batal
  (plus pilihan cepat / tombol khusus bila ada: angka, pilih channel/kontak).
  Membatalkan cukup satu ketukan tombol — tidak perlu mengetik perintah;
- ReplyKeyboardRemove → membersihkan reply keyboard saat tak lagi dipakai.
"""
from pyrogram import enums
from pyrogram.types import (
    InlineKeyboardButton,
    KeyboardButton,
    KeyboardButtonRequestUsers,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

PLACEHOLDER_MAX = 64  # batas Telegram untuk input_field_placeholder


def _ph(placeholder: str = None) -> str | None:
    """Amankan placeholder agar tak melebihi batas Telegram."""
    if not placeholder:
        return None
    return placeholder[:PLACEHOLDER_MAX]


# Tombol batal universal pada keyboard input kontekstual — teksnya adalah kuncinya
CANCEL = "🗑 Batal"


# ---------------------------------------------------------------- tombol berwarna

def btn(text: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=cb)


def primary(text: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=cb, style=enums.ButtonStyle.PRIMARY)


def success(text: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=cb, style=enums.ButtonStyle.SUCCESS)


def danger(text: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=cb, style=enums.ButtonStyle.DANGER)


def copybtn(text: str, value: str) -> InlineKeyboardButton:
    """Salin ke clipboard sekali ketuk."""
    return InlineKeyboardButton(text, copy_text=value)


def urlbtn(text: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, url=url)


def sharebtn(text: str, query: str = "") -> InlineKeyboardButton:
    """Buka pemilih chat lalu kirim kartu inline bot (viral loop)."""
    return InlineKeyboardButton(text, switch_inline_query=query)


# ---------------------------------------------------------------- reply markup

def input_keyboard(choices: list = None, placeholder: str = None) -> ReplyKeyboardMarkup:
    """Keyboard input kontekstual: muncul hanya saat bot menunggu jawaban,
    selalu membawa tombol 🗑 Batal, dan ditutup otomatis setelah selesai.

    `choices`: baris-baris pilihan cepat — berisi string atau KeyboardButton
    (mis. tombol request_chat). Jawaban tetap bisa diketik bebas.
    """
    rows = []
    for row in choices or []:
        rows.append([b if isinstance(b, KeyboardButton) else KeyboardButton(str(b)) for b in row])
    rows.append([KeyboardButton(CANCEL)])
    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        one_time_keyboard=True,
        placeholder=_ph(placeholder),
    )


def contact_picker(text: str, button_id: int, bots_ok: bool = True) -> KeyboardButton:
    """Tombol reply-keyboard 'pilih kontak' (request_users) — dipakai saat butuh
    memilih pengguna langsung dari daftar kontak Telegram (mis. lupa ID/username)."""
    return KeyboardButton(
        text,
        request_users=KeyboardButtonRequestUsers(
            button_id=button_id,
            user_is_bot=None if bots_ok else False,
            request_name=True,
            request_username=True,
        ),
    )


def remove_keyboard() -> ReplyKeyboardRemove:
    """Pakai saat reply keyboard tidak lagi dibutuhkan — layar bersih kembali."""
    return ReplyKeyboardRemove()


# ---------------------------------------------------------------- elemen teks

def title(text: str, emoji: str = "") -> str:
    """Judul layar — maksimal satu emoji, tebal."""
    return f"{emoji} <b>{text}</b>".strip()


def field(label: str, value) -> str:
    """Baris info kalem: 'Poin hari ini · <b>1/3</b>'."""
    return f"{label} · <b>{value}</b>"


def muted(text: str) -> str:
    """Teks sekunder yang kurang menonjol."""
    return f"<i>{text}</i>"


_CHIP = {
    "draft": "📝 Draf",
    "review": "🕵️ Direview",
    "sent": "✅ Terkirim",
    "rejected": "🚫 Ditolak",
    "failed": "❌ Gagal",
    "cancelled": "🗑 Dibatalkan",
    "pending": "⏳ Menunggu",
    "delivered": "💌 Tampil",
    "stopped": "😶 Dihentikan",
}


def chip(status: str) -> str:
    return _CHIP.get(status, status)


def quote(text: str, expandable: bool = False) -> str:
    """Bungkus HTML dalam blockquote Telegram."""
    tag = "blockquote expandable" if expandable else "blockquote"
    return f"<{tag}>{text}</blockquote>"
