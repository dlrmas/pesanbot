"""Router terpadu — satu pintu untuk callback, state FSM, dan command.

Pola sarang laba-laba: semua fitur mendaftar lewat dekorator @cb / @state /
@command; hanya ada SATU MessageHandler dan SATU CallbackQueryHandler di bot.
Guard peran, ban, dan error ditangani di satu tempat.
"""
import logging

from pyrogram import filters
from pyrogram.handlers import CallbackQueryHandler, MessageHandler

from app import db
from app.core import users as core_users
from app.core import utils

log = logging.getLogger("router")

_cb: dict[tuple[str, str], tuple] = {}
_st: dict[str, callable] = {}
_cmd: dict[str, tuple] = {}
_nav: dict[str, callable] = {}


def cb(ns: str, action: str, admin: bool = False, owner: bool = False):
    """Daftarkan handler callback untuk data 'ns:action[:arg:...]'."""
    def deco(fn):
        _cb[(ns, action)] = (fn, admin, owner)
        return fn
    return deco


def state(name: str):
    """Daftarkan handler input pesan saat pengguna berada di state tertentu."""
    def deco(fn):
        _st[name] = fn
        return fn
    return deco


def command(name: str, admin: bool = False, owner: bool = False):
    def deco(fn):
        _cmd[name] = (fn, admin, owner)
        return fn
    return deco


def nav(text: str):
    """Daftarkan tombol navigasi reply keyboard (cocok teks persis)."""
    def deco(fn):
        _nav[text] = fn
        return fn
    return deco


# ---- penyimpanan state FSM (Mongo, tahan restart) ----

async def set_state(uid: int, name: str, **data):
    await db.states.replace_one(
        {"_id": uid}, {"_id": uid, "name": name, "data": data, "at": utils.now()}, upsert=True
    )


async def get_state(uid: int) -> dict | None:
    return await db.states.find_one({"_id": uid})


async def clear_state(uid: int):
    await db.states.delete_one({"_id": uid})


# ---- dispatch ----

async def _allowed(role: str | None, need_admin: bool, need_owner: bool) -> str | None:
    if need_owner and role != "owner":
        return "🔒 Khusus Owner."
    if need_admin and role not in ("owner", "mod"):
        return "🔒 Khusus admin."
    return None


async def _dispatch_cb(client, cq):
    parts = (cq.data or "").split(":")
    if len(parts) < 2:
        return await _safe_answer(cq)
    entry = _cb.get((parts[0], parts[1]))
    if not entry:
        return await _safe_answer(cq, "Tombol ini sudah tidak aktif.")
    fn, need_admin, need_owner = entry
    try:
        user = await core_users.ensure(cq.from_user)
        if user.get("banned"):
            return await _safe_answer(cq, "🚫 Aksesmu dibatasi.", alert=True)
        role = await core_users.get_role(user["_id"])
        denied = await _allowed(role, need_admin, need_owner)
        if denied:
            return await _safe_answer(cq, denied, alert=True)
        await fn(client, cq, parts[2:], user)
        await _safe_answer(cq)
    except Exception:
        log.exception("callback %s gagal", cq.data)
        await _safe_answer(cq, "⚠️ Terjadi kesalahan, coba lagi.", alert=True)
        await _alert_admins(client, f"callback {cq.data}")


async def _dispatch_msg(client, m):
    if not m.from_user or m.from_user.is_bot:
        return
    try:
        user = await core_users.ensure(m.from_user)
        if user.get("banned"):
            return
        text = m.text or ""
        if text.startswith("/"):
            name = text.split()[0][1:].split("@")[0].lower()
            payload = text.split(maxsplit=1)[1] if " " in text else ""
            entry = _cmd.get(name)
            if entry:
                fn, need_admin, need_owner = entry
                role = await core_users.get_role(user["_id"])
                if await _allowed(role, need_admin, need_owner):
                    return
                return await fn(client, m, user, payload)
            from app.core import screens
            from app.features import start
            await screens.drop(client, m)
            return await start.show_main(client, user)
        if text in _nav:
            from app.core import screens
            await screens.drop(client, m)
            return await _nav[text](client, m, user)
        st = await get_state(user["_id"])
        if st and st.get("name") in _st:
            return await _st[st["name"]](client, m, user, st)
        from app.core import screens
        from app.features import start
        await screens.drop(client, m)
        await start.show_main(client, user)
    except Exception:
        log.exception("message dispatch gagal")
        try:  # beri tahu pengguna (best effort) — selaras dengan jalur callback
            from app.core import screens
            await screens.notify(client, m.from_user.id, "⚠️ Terjadi kesalahan, coba lagi ya.")
        except Exception:
            pass
        await _alert_admins(client, "message dispatch")


async def _safe_answer(cq, text: str = None, alert: bool = False):
    try:
        await cq.answer(text, show_alert=alert)
    except Exception:
        pass


async def _alert_admins(client, where: str):
    """Teruskan kejadian error ke admin (throttled di panel) — best effort."""
    try:
        from app.features.admin import panel
        await panel.ping_error(client, where)
    except Exception:
        pass


def attach(bot):
    # Service message diabaikan, KECUALI hasil tombol picker request_chat /
    # request_users (chat_shared / users_shared) — itu input nyata yang harus
    # sampai ke state handler (mis. pemilihan channel menfes).
    msg_filter = filters.private & (
        ~filters.service | filters.chat_shared | filters.users_shared
    )
    bot.add_handler(MessageHandler(_dispatch_msg, msg_filter))
    bot.add_handler(CallbackQueryHandler(_dispatch_cb))
