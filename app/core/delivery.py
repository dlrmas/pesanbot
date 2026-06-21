"""Mesin pengiriman — satu sumber untuk: menfes→channel, confes→target,
balasan anonim→lawan thread. Tidak pernah meneruskan pesan asli (anti-jejak).
"""
import logging

from pyrogram.types import InlineKeyboardMarkup

from app import clients, db
from app.assistant import runner
from app.core import content as content_mod
from app.core import hashtags, screens, ui, utils

log = logging.getLogger("delivery")


# ---------------------------------------------------------------- menfes

async def post_menfes(bot, draft: dict, s: dict):
    """-> (ok, link|None, err). Identitas pengirim tidak pernah ikut terkirim."""
    ch = s.get("channel_id")
    if not ch:
        return False, None, "Channel menfes belum diatur admin."
    meta = []
    mood = draft.get("mood")
    if mood:
        meta.append(f"{mood['emoji']} <i>{utils.esc(mood['label'])}</i>")
    tags = draft.get("tags") or []
    if tags:
        dmap = await hashtags.docs_map(tags)
        meta.append(utils.esc(hashtags.render(tags, dmap)))
    footer = ui.quote("\n".join(meta)) if meta else None
    try:
        m = await content_mod.send(bot, ch, draft["content"], extra=footer)
    except Exception as e:
        log.warning("post_menfes gagal: %s", e)
        return False, None, f"{type(e).__name__}"
    link = None
    try:
        link = m.link
    except Exception:
        pass
    await db.posts.insert_one({
        "draft_id": draft["_id"], "user_id": draft["user_id"],
        "chat_id": ch, "msg_id": m.id, "tags": tags, "link": link,
        "mood": (mood or {}).get("key"), "created_at": utils.now(),
    })
    return True, link, None


# ---------------------------------------------------------------- confes

def _confes_kb(conf_id) -> InlineKeyboardMarkup:
    cid = str(conf_id)
    return InlineKeyboardMarkup([
        [ui.primary("↩️ Balas Anonim", f"r:new:{cid}")],
        [
            ui.btn("🚩 Lapor", f"r:repc:{cid}"),
            ui.danger("🚫 Blokir Sumber", f"r:blkc:{cid}"),
        ],
    ])


async def queue_confes(bot, sender: dict, draft: dict, s: dict):
    """Proses kirim confes. -> (code, teks_untuk_pengirim, effect|None).

    code: delivered | pending | stopped
    """
    t = draft["target"]
    tid, uid = t["id"], sender["_id"]

    if await db.blocks.find_one({"target_id": tid, "sender_id": uid}):
        return "stopped", "🚫 Pesan tidak dapat dikirim ke target ini.", None

    tdoc = await db.targets.find_one({"_id": tid}) or {}
    prefs = (await db.users.find_one({"_id": tid}) or {}).get("inbox", {})
    if prefs.get("enabled") is False or tdoc.get("status") in ("declined", "unreachable"):
        return "stopped", "😶 Target tidak menerima confes saat ini.", None
    if prefs.get("text_only") and draft["content"]["type"] != "text":
        return "stopped", "📝 Target hanya menerima confes berupa teks.", None

    n_target = await db.confessions.count_documents({"target_id": tid, "status": "pending"})
    if n_target >= s["max_pending_per_target"]:
        return "stopped", "📪 Antrean confes target sudah penuh, coba lagi nanti.", None
    n_pair = await db.confessions.count_documents(
        {"target_id": tid, "sender_id": uid, "status": "pending"}
    )
    if n_pair >= s["max_pending_per_pair"]:
        return "stopped", "⏳ Masih ada confes-mu yang menunggu dibaca target ini.", None

    res = await db.confessions.insert_one({
        "sender_id": uid, "target_id": tid, "target_name": t.get("name", ""),
        "content": draft["content"], "status": "pending", "created_at": utils.now(),
    })
    conf_id = res.inserted_id

    paused = utils.aware(prefs.get("paused_until"))
    can_now = tdoc.get("status") == "accepted" and not (paused and paused > utils.now())
    if can_now:
        ok = await deliver_confes(bot, await db.confessions.find_one({"_id": conf_id}))
        if ok:
            return "delivered", "💌 Confes terkirim & sudah tampil di chat target! 🤫", "sent"
        return "pending", "💌 Confes disimpan — target sedang tidak terjangkau, akan tampil saat ia kembali.", None

    # target belum menerima: peringatan asisten HANYA SATU KALI seumur target
    if not tdoc.get("warned"):
        token = tdoc.get("token") or utils.gen_token(12)
        await db.targets.update_one(
            {"_id": tid},
            {"$set": {"token": token, "username": t.get("username"),
                      "name": t.get("name", ""), "status": "pending"},
             "$setOnInsert": {"created_at": utils.now()}},
            upsert=True,
        )
        ok, why = await runner.send_warning({"id": tid, "username": t.get("username")}, token)
        if ok:
            await db.targets.update_one(
                {"_id": tid}, {"$set": {"warned": True, "warned_at": utils.now()}}
            )
            return ("pending",
                    "💌 Confes disimpan! Asisten sudah memberi tahu target satu kali. "
                    "Begitu target menerima, confes-mu langsung tampil.", "sent")
        if why == "unreachable":
            await db.targets.update_one({"_id": tid}, {"$set": {"status": "unreachable"}})
            await db.confessions.update_one({"_id": conf_id}, {"$set": {"status": "stopped"}})
            return "stopped", "😕 Target tidak dapat dihubungi asisten (kemungkinan pengaturan privasi).", None
        return ("pending",
                "💌 Confes masuk antrean. Asisten sedang tidak aktif — target akan "
                "dihubungi saat asisten kembali menyala.", None)

    return ("pending",
            "💌 Confes masuk antrean target (target sudah pernah diberi tahu, "
            "tinggal menunggu ia membukanya).", "sent")


async def deliver_confes(bot, conf: dict) -> bool:
    head = "💌 <b>Kamu menerima confes anonim</b>"
    hint = "Ketuk untuk membuka" if conf["content"]["type"] != "voice" \
        else "Putar untuk mendengarkan"
    extra = ui.quote(
        f"{hint} · pengirim dirahasiakan\n"
        f"Dikirim lewat @{clients.bot_username} — bisa kamu balas, lapor, atau blokir."
    )
    try:
        await screens.typing(bot, conf["target_id"])
        await content_mod.send(
            bot, conf["target_id"], conf["content"],
            head=head, extra=extra, kb=_confes_kb(conf["_id"]), spoiler=True,
        )
    except Exception as e:
        log.info("deliver_confes gagal ke %s: %s", conf["target_id"], e)
        return False
    await db.confessions.update_one(
        {"_id": conf["_id"]}, {"$set": {"status": "delivered", "delivered_at": utils.now()}}
    )
    await screens.notify(
        bot, conf["sender_id"],
        "💌 <b>Confes-mu sudah tampil di chat target.</b>", effect="sent",
    )
    return True


async def flush_pending(bot, target_id: int) -> int:
    """Tampilkan semua confes pending saat target menerima / kembali aktif."""
    n = 0
    cursor = db.confessions.find({"target_id": target_id, "status": "pending"}).sort("created_at", 1)
    async for conf in cursor:
        if await deliver_confes(bot, conf):
            n += 1
    return n


async def retry_pending_warnings(bot, limit: int = 30) -> int:
    """Kirim peringatan untuk target yang punya confes pending tapi belum sempat
    di-warn (mis. asisten sedang mati saat confes masuk). Dipanggil saat asisten
    kembali aktif. Berhenti bila asisten kena limit lagi. -> jumlah berhasil."""
    s = await db.get_settings(fresh=True)
    if not s.get("assistant_enabled") or not clients.assistant:
        return 0
    n = 0
    cursor = db.targets.find({"status": "pending", "warned": {"$ne": True}}).limit(limit)
    async for t in cursor:
        tid = t["_id"]
        if not await db.confessions.count_documents({"target_id": tid, "status": "pending"}):
            continue  # tak ada lagi yang menunggu
        token = t.get("token") or utils.gen_token(12)
        if not t.get("token"):
            await db.targets.update_one({"_id": tid}, {"$set": {"token": token}})
        ok, why = await runner.send_warning({"id": tid, "username": t.get("username")}, token)
        if ok:
            await db.targets.update_one(
                {"_id": tid}, {"$set": {"warned": True, "warned_at": utils.now()}}
            )
            n += 1
        elif why == "limit":
            break  # asisten kembali dimatikan otomatis — hentikan
        elif why == "unreachable":
            await db.targets.update_one({"_id": tid}, {"$set": {"status": "unreachable"}})
    return n


async def stop_target(bot, target_id: int, status: str):
    """Target menolak/blokir: hentikan semua pengiriman & beri tahu pengirim."""
    await db.targets.update_one(
        {"_id": target_id}, {"$set": {"status": status}}, upsert=True
    )
    cursor = db.confessions.find({"target_id": target_id, "status": "pending"})
    async for conf in cursor:
        await db.confessions.update_one({"_id": conf["_id"]}, {"$set": {"status": "stopped"}})
        await screens.notify(
            bot, conf["sender_id"],
            "😶 Target memilih tidak menerima confes. Pengiriman ke target ini dihentikan.",
        )


# ---------------------------------------------------------------- balas anonim

def thread_kb(tid) -> InlineKeyboardMarkup:
    t = str(tid)
    return InlineKeyboardMarkup([
        [ui.primary("↩️ Balas Anonim", f"r:msg:{t}")],
        [
            ui.btn("⛔ Stop", f"r:stop:{t}"),
            ui.btn("🚩 Lapor", f"r:rep:{t}"),
            ui.danger("🚫 Blokir", f"r:blk:{t}"),
        ],
    ])


async def relay_reply(bot, thread: dict, from_uid: int, content: dict):
    """Teruskan balasan ke pihak lain tanpa membuka identitas. -> (ok, err)."""
    if thread.get("status") != "active":
        return False, "Thread sudah ditutup."
    exp = utils.aware(thread.get("expire_at"))
    if exp and exp < utils.now():
        await db.threads.update_one({"_id": thread["_id"]}, {"$set": {"status": "expired"}})
        return False, "Thread sudah kedaluwarsa."
    is_a = from_uid == thread["a_id"]
    to_uid = thread["b_id"] if is_a else thread["a_id"]
    alias = "Anon A" if is_a else "Anon B"
    head = f"💬 <b>{alias} membalas</b>"
    extra = ui.quote(f"Percakapan anonim dua arah · via @{clients.bot_username}")
    try:
        await screens.typing(bot, to_uid)
        await content_mod.send(bot, to_uid, content, head=head, extra=extra,
                               kb=thread_kb(thread["_id"]))
    except Exception as e:
        log.info("relay gagal: %s", e)
        await db.threads.update_one({"_id": thread["_id"]}, {"$set": {"status": "closed"}})
        return False, "Lawan bicara tidak dapat dijangkau — thread ditutup."
    side = "a" if is_a else "b"
    await db.threads.update_one(
        {"_id": thread["_id"]},
        {"$inc": {f"count_{side}": 1}, "$set": {"last_at": utils.now()}},
    )
    return True, None
