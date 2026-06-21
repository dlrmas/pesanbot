"""Admin (Owner): broadcast massal via bot, asisten, atau keduanya.

Memakai mesin drafts (preview + konfirmasi ganda), rate limit ketat, dan log.
"""
import asyncio
import logging

from pyrogram.errors import FloodWait
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app import clients, db
from app.core import audit, content as content_mod
from app.core import drafts, router, screens, utils

log = logging.getLogger("broadcast")

AUD_LABEL = {"bot": "🤖 via Bot", "asisten": "👤 via Asisten", "both": "📡 Keduanya"}


@router.cb("a", "bc", owner=True)
async def _cb_broadcast(client, cq, args, user):
    sub = args[0] if args else "home"
    if sub == "aud":
        aud = args[1]
        s = await db.get_settings()
        if aud in ("asisten", "both") and (not clients.assistant or not s["assistant_enabled"]):
            return await cq.answer("🤖 Asisten sedang tidak aktif.", show_alert=True)
        draft = await drafts.begin(user["_id"], "bcast", audience=aud)
        return await drafts.prompt_content(
            client, user, draft,
            note=f"📣 Broadcast {AUD_LABEL[aud]}\n"
                 "ℹ️ Jalur asisten hanya mendukung <b>teks</b>.",
        )
    rows = [
        [InlineKeyboardButton(AUD_LABEL["bot"], callback_data="a:bc:aud:bot")],
        [InlineKeyboardButton(AUD_LABEL["asisten"], callback_data="a:bc:aud:asisten")],
        [InlineKeyboardButton(AUD_LABEL["both"], callback_data="a:bc:aud:both")],
        [InlineKeyboardButton("🛠 Panel", callback_data="a:home")],
    ]
    last = await db.broadcasts.find_one({}, sort=[("at", -1)])
    if last:
        run_tag = " · ⏳ berjalan" if last.get("status") == "running" else ""
        info = f"Terakhir: {utils.fmt_dt(last['at'])} · ✅{last.get('ok', 0)} ❌{last.get('fail', 0)}{run_tag}"
    else:
        info = "Belum pernah broadcast."
    await screens.show(
        client, user["_id"],
        f"📣 <b>Broadcast</b>\n{info}\n\nPilih jalur pengiriman:",
        InlineKeyboardMarkup(rows),
    )


_SAVE_EVERY = 25  # simpan progress tiap N pengguna agar bisa dilanjutkan usai restart


async def run(bot, draft: dict):
    """Dipanggil drafts.finalize setelah konfirmasi ganda — jalan di background.

    Catatan broadcast disimpan dulu (status 'running') supaya bisa dilanjutkan
    bila proses mati di tengah jalan (lihat resume_pending)."""
    c = draft["content"]
    doc = {
        "by": draft["user_id"], "audience": draft.get("audience", "bot"),
        "content": c, "type": c["type"], "status": "running",
        "ok": 0, "fail": 0, "last_id": None,
        "started_at": utils.now(), "at": utils.now(),
    }
    res = await db.broadcasts.insert_one(doc)
    doc["_id"] = res.inserted_id
    asyncio.create_task(_run(bot, doc))


async def _run(bot, bc: dict):
    aud = bc.get("audience", "bot")
    owner_id = bc["by"]
    c = bc["content"]
    ok, fail = bc.get("ok", 0), bc.get("fail", 0)
    last_id = bc.get("last_id")

    plain = None
    if aud in ("asisten", "both") and c["type"] == "text":
        plain = c["text"]

    # urut _id menaik supaya bisa lanjut dari last_id setelah restart
    q = {"banned": False}
    if last_id is not None:
        q["_id"] = {"$gt": last_id}
    since_save = 0
    async for u in db.users.find(q, {"_id": 1}).sort("_id", 1):
        uid = u["_id"]
        sent = False
        if aud in ("bot", "both"):
            try:
                await content_mod.send(bot, uid, c)
                sent = True
            except FloodWait as e:
                await asyncio.sleep(e.value + 1)
            except Exception:
                pass
            await asyncio.sleep(0.08)  # patuh limit bot API
        if aud in ("asisten", "both") and plain and clients.assistant:
            try:
                await clients.assistant.send_message(uid, plain)
                sent = True
                await asyncio.sleep(1.5)  # akun asisten jauh lebih ketat
            except FloodWait:
                log.warning("asisten kena FloodWait — jalur asisten dihentikan")
                plain = None  # berhenti memakai asisten, patuh limit
            except Exception:
                pass
        ok += 1 if sent else 0
        fail += 0 if sent else 1
        last_id = uid
        since_save += 1
        if since_save >= _SAVE_EVERY:
            await db.broadcasts.update_one(
                {"_id": bc["_id"]},
                {"$set": {"ok": ok, "fail": fail, "last_id": last_id}},
            )
            since_save = 0

    await db.broadcasts.update_one(
        {"_id": bc["_id"]},
        {"$set": {"status": "done", "ok": ok, "fail": fail,
                  "last_id": last_id, "at": utils.now()}},
    )
    await audit.log("broadcast", actor=owner_id, audience=aud, ok=ok, fail=fail)
    await screens.notify(
        bot, owner_id,
        f"📣 <b>Broadcast selesai</b> ({AUD_LABEL.get(aud, aud)})\n"
        f"✅ Terkirim: {ok} · ❌ Gagal: {fail}",
        effect="success",
    )


async def resume_pending(bot):
    """Lanjutkan broadcast yang terputus karena restart (status masih 'running')."""
    async for bc in db.broadcasts.find({"status": "running"}):
        log.info("melanjutkan broadcast %s (lewat %s)", bc["_id"], bc.get("last_id"))
        asyncio.create_task(_run(bot, bc))
