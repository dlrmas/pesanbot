"""Balas anonim — percakapan dua arah tanpa identitas (CLAUDE.md §8).

Thread sementara (kedaluwarsa otomatis), alias Anon A (pengirim) / Anon B
(target), tombol Stop / Lapor / Blokir di setiap pesan yang diteruskan.
"""
from datetime import timedelta

from pyrogram.types import InlineKeyboardMarkup

from app import db
from app.core import audit, drafts, ratelimit, router, screens, ui, utils

_CONFIRM_TTL_NOTE = "Tekan sekali lagi untuk konfirmasi."


async def _get_thread(tid, uid: int) -> dict | None:
    t = await db.threads.find_one({"_id": utils.oid(tid)})
    if not t or uid not in (t["a_id"], t["b_id"]):
        return None
    return t


def _other(thread: dict, uid: int) -> int:
    return thread["b_id"] if uid == thread["a_id"] else thread["a_id"]


async def _begin_reply(client, user: dict, thread: dict):
    s = await db.get_settings()
    left = ratelimit.cooldown_left(user, "reply", s)
    if left:
        return await screens.show(
            client, user["_id"],
            f"⏳ Tunggu <b>{utils.left_str(left)}</b> sebelum membalas lagi.",
            drafts.home_kb(),
        )
    draft = await drafts.begin(user["_id"], "reply", thread_id=str(thread["_id"]))
    await drafts.prompt_content(
        client, user, draft,
        note="🤫 Balasanmu diteruskan <b>tanpa identitas</b>.",
    )


@router.cb("r", "new")
async def _cb_reply_to_confes(client, cq, args, user):
    """Target menekan 'Balas Anonim' pada confes yang ia terima."""
    conf = await db.confessions.find_one({"_id": utils.oid(args[0])})
    if not conf or conf["target_id"] != user["_id"]:
        return await cq.answer("Pesan ini bukan untukmu.", show_alert=True)
    thread = await db.threads.find_one({"conf_id": conf["_id"], "status": "active"})
    if not thread:
        s = await db.get_settings()
        doc = {
            "conf_id": conf["_id"],
            "a_id": conf["sender_id"], "b_id": conf["target_id"],
            "status": "active", "count_a": 0, "count_b": 0,
            "created_at": utils.now(),
            "expire_at": utils.now() + timedelta(hours=s["thread_ttl_h"]),
        }
        res = await db.threads.insert_one(doc)
        doc["_id"] = res.inserted_id
        thread = doc
    await _begin_reply(client, user, thread)


@router.cb("r", "msg")
async def _cb_reply_in_thread(client, cq, args, user):
    thread = await _get_thread(args[0], user["_id"])
    if not thread:
        return await cq.answer("Thread tidak ditemukan.", show_alert=True)
    if thread["status"] != "active":
        return await cq.answer("Thread sudah ditutup.", show_alert=True)
    exp = utils.aware(thread.get("expire_at"))
    if exp and exp < utils.now():
        await db.threads.update_one({"_id": thread["_id"]}, {"$set": {"status": "expired"}})
        return await cq.answer("Thread sudah kedaluwarsa.", show_alert=True)
    await _begin_reply(client, user, thread)


@router.cb("r", "stop")
async def _cb_stop(client, cq, args, user):
    thread = await _get_thread(args[0], user["_id"])
    if not thread:
        return await cq.answer("Thread tidak ditemukan.", show_alert=True)
    if len(args) < 2 or args[1] != "yes":  # konfirmasi kedua
        try:
            await cq.edit_message_reply_markup(InlineKeyboardMarkup([[
                ui.danger("⛔ Yakin, hentikan thread", f"r:stop:{args[0]}:yes"),
                ui.btn("◀️ Tidak jadi", f"r:keep:{args[0]}"),
            ]]))
        except Exception:
            pass
        return await cq.answer(_CONFIRM_TTL_NOTE)
    await db.threads.update_one({"_id": thread["_id"]}, {"$set": {"status": "closed"}})
    await _clear_markup(cq)
    await cq.answer("Thread dihentikan.")
    for uid in (thread["a_id"], thread["b_id"]):
        await screens.notify(client, uid, "⛔ <b>Thread anonim dihentikan.</b> Terima kasih sudah saling jaga. 🤍")


@router.cb("r", "keep")
async def _cb_keep(client, cq, args, user):
    thread = await _get_thread(args[0], user["_id"])
    if thread:
        try:
            from app.core.delivery import thread_kb
            await cq.edit_message_reply_markup(thread_kb(thread["_id"]))
        except Exception:
            pass
    await cq.answer("Oke, thread tetap berjalan.")


@router.cb("r", "rep")
async def _cb_report_thread(client, cq, args, user):
    thread = await _get_thread(args[0], user["_id"])
    if not thread:
        return await cq.answer("Thread tidak ditemukan.", show_alert=True)
    await _file_report(client, user, against=_other(thread, user["_id"]),
                       kind="thread", ref=str(thread["_id"]))
    await db.threads.update_one({"_id": thread["_id"]}, {"$set": {"status": "reported"}})
    await cq.answer("🚩 Laporan terkirim ke admin. Thread ditandai.", show_alert=True)


@router.cb("r", "blk")
async def _cb_block_thread(client, cq, args, user):
    thread = await _get_thread(args[0], user["_id"])
    if not thread:
        return await cq.answer("Thread tidak ditemukan.", show_alert=True)
    if len(args) < 2 or args[1] != "yes":
        try:
            await cq.edit_message_reply_markup(InlineKeyboardMarkup([[
                ui.danger("🚫 Yakin, blokir", f"r:blk:{args[0]}:yes"),
                ui.btn("◀️ Tidak jadi", f"r:keep:{args[0]}"),
            ]]))
        except Exception:
            pass
        return await cq.answer(_CONFIRM_TTL_NOTE)
    other = _other(thread, user["_id"])
    await _block_pair(user["_id"], other)
    await db.threads.update_one({"_id": thread["_id"]}, {"$set": {"status": "closed"}})
    await _clear_markup(cq)
    await cq.answer("🚫 Sumber diblokir. Ia tidak bisa menghubungimu lagi.", show_alert=True)


# ---- aksi pada pesan confes (sebelum ada thread) ----

@router.cb("r", "repc")
async def _cb_report_confes(client, cq, args, user):
    conf = await db.confessions.find_one({"_id": utils.oid(args[0])})
    if not conf or conf["target_id"] != user["_id"]:
        return await cq.answer("Pesan ini bukan untukmu.", show_alert=True)
    await _file_report(client, user, against=conf["sender_id"],
                       kind="confes", ref=str(conf["_id"]))
    await cq.answer("🚩 Laporan terkirim ke admin.", show_alert=True)


@router.cb("r", "blkc")
async def _cb_block_confes(client, cq, args, user):
    conf = await db.confessions.find_one({"_id": utils.oid(args[0])})
    if not conf or conf["target_id"] != user["_id"]:
        return await cq.answer("Pesan ini bukan untukmu.", show_alert=True)
    if len(args) < 2 or args[1] != "yes":
        try:
            await cq.edit_message_reply_markup(InlineKeyboardMarkup([[
                ui.danger("🚫 Yakin, blokir sumber", f"r:blkc:{args[0]}:yes"),
            ]]))
        except Exception:
            pass
        return await cq.answer(_CONFIRM_TTL_NOTE)
    await _block_pair(user["_id"], conf["sender_id"])
    await _clear_markup(cq)
    await cq.answer("🚫 Sumber diblokir. Ia tidak bisa mengirim confes lagi padamu.", show_alert=True)


# ---- helper bersama ----

async def _clear_markup(cq):
    """Nonaktifkan tombol lama setelah aksi final agar tidak diproses ulang."""
    try:
        await cq.edit_message_reply_markup(None)
    except Exception:
        pass


async def _block_pair(target_id: int, sender_id: int):
    try:
        await db.blocks.insert_one(
            {"target_id": target_id, "sender_id": sender_id, "at": utils.now()}
        )
    except Exception:
        pass  # sudah diblokir
    await audit.log("block", actor=target_id)  # identitas sumber cukup di audit


async def _file_report(client, reporter: dict, against: int, kind: str, ref: str):
    await db.reports.insert_one({
        "kind": kind, "ref": ref, "by": reporter["_id"], "against": against,
        "status": "open", "at": utils.now(),
    })
    await audit.strike(against, "reports")
    await audit.log("report", actor=reporter["_id"], target=against, kind=kind, ref=ref)
    from app.features.admin import panel
    await panel.ping_reports(client)
