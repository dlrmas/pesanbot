"""Alur confes: pilih target → konten → preview → pending → peringatan asisten
satu kali → target menerima/menolak. Plus kontrol inbox untuk target."""
from datetime import timedelta

from pyrogram.enums import ChatType
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app import db
from app.core import audit, delivery, drafts, ratelimit, router, screens, ui, utils
from app.core import users as core_users


@router.cb("c", "new")
async def _cb_new(client, cq, args, user):
    await begin_flow(client, user)


async def begin_flow(client, user: dict):
    s = await db.get_settings()
    if not s["confes_enabled"] or s["paused"]:
        return await screens.show(
            client, user["_id"], "😴 Fitur confes sedang nonaktif. Coba lagi nanti ya.",
            drafts.home_kb(),
        )
    left = ratelimit.cooldown_left(user, "confes", s)
    if left:
        return await screens.show(
            client, user["_id"],
            f"⏳ Pelan-pelan! Confes berikutnya bisa dikirim dalam <b>{utils.left_str(left)}</b>.",
            drafts.home_kb(),
        )
    draft = await drafts.begin(user["_id"], "confes")
    await prompt_target(client, user, draft)


async def prompt_target(client, user: dict, draft: dict, note: str = None):
    await router.set_state(user["_id"], "draft.target", draft_id=str(draft["_id"]))
    # tombol pilih kontak (request_users) — bots_ok=False memastikan bukan bot
    picker = ui.contact_picker("👤 Pilih Kontak…", button_id=8, bots_ok=False)
    text = (
        "🎯 <b>Siapa target confes-mu?</b>\n"
        "Ketuk <b>👤 Pilih Kontak…</b> di bawah, atau balas dengan "
        "<code>@username</code>, ID Telegram, atau link <code>t.me/…</code>\n"
        "💡 Confes <b>gratis</b> & identitasmu tetap rahasia."
    )
    if note:
        text = f"{note}\n\n{text}"
    await screens.ask(
        client, user["_id"], text,
        placeholder="@username · 12345678 · t.me/…",
        choices=[[picker]],
    )


async def _vet_target(tid: int, name: str, username, user: dict) -> tuple[dict | None, str | None]:
    """Periksa kelayakan target (dipakai jalur tombol & teks). (target, err)."""
    if tid == user["_id"]:
        return None, "😅 Tidak bisa confes ke diri sendiri."
    tu = await db.users.find_one({"_id": tid})
    if tu and tu.get("banned"):
        return None, "🚫 Target tidak tersedia saat ini."
    if tu and tu.get("inbox", {}).get("enabled") is False:
        return None, "😶 Orang ini menonaktifkan confes untuk dirinya."
    return {"id": tid, "name": name or "Seseorang", "username": username}, None


async def _resolve_target(client, m, user: dict) -> tuple[dict | None, str | None]:
    """Resolusi target dari tombol Pilih Kontak (users_shared) atau teks bebas."""
    if m.users_shared and m.users_shared.users:
        su = m.users_shared.users[0]
        tid = getattr(su, "id", None)
        if not tid:
            return None, "🔍 Gagal membaca kontak. Coba lagi."
        chat = None
        try:  # nama/username terbaru bila bot bisa mengaksesnya
            chat = await client.get_chat(tid)
        except Exception:
            pass
        name = getattr(chat, "first_name", None) or getattr(su, "first_name", None)
        username = getattr(chat, "username", None) or getattr(su, "username", None)
        return await _vet_target(tid, name, username, user)

    raw = (m.text or "").strip()
    raw = raw.replace("https://", "").replace("http://", "")
    if raw.lower().startswith("t.me/"):
        raw = raw[5:]
    raw = raw.strip().lstrip("@").split("?")[0].strip("/")
    chat = None
    try:
        chat = await client.get_chat(utils.parse_int(raw) or raw)
    except Exception:
        pass
    if not chat or chat.type not in (ChatType.PRIVATE,):
        return None, "🔍 Target tidak ditemukan. Pastikan username/ID benar."
    return await _vet_target(chat.id, chat.first_name, chat.username, user)


@router.state("draft.target")
async def _st_target(client, m, user, st):
    draft = await drafts.active_or_expire(client, m, user, st)
    if not draft:
        return
    target, err = await _resolve_target(client, m, user)
    if err:
        return await prompt_target_err(client, user, draft, err)

    await drafts.patch(draft["_id"], target=target)
    draft["target"] = target
    if draft.get("content"):
        return await drafts.show_preview(client, user, draft)
    await drafts.prompt_content(
        client, user, draft, note=f"✅ Target: <b>{utils.esc(target['name'])}</b>"
    )


async def prompt_target_err(client, user, draft, note):
    # pakai ulang prompt yang sama (tombol Pilih Kontak tetap ada) — konsisten
    await prompt_target(client, user, draft, note=note)


# ---------------------------------------------------------------- target menerima / menolak

async def accept_token(client, user: dict, token: str):
    tdoc = await db.targets.find_one({"token": token})
    if not tdoc or tdoc["_id"] != user["_id"]:
        from app.features import start
        return await start.show_main(client, user, note="🤔 Link ini bukan untukmu.")
    await db.targets.update_one(
        {"_id": user["_id"]}, {"$set": {"status": "accepted", "accepted_at": utils.now()}}
    )
    await audit.log("confes_accepted", actor=user["_id"])
    n = await delivery.flush_pending(client, user["_id"])
    kb = drafts.home_kb([[InlineKeyboardButton("📥 Atur Inbox Confes", callback_data="ib:home")]])
    await screens.show(
        client, user["_id"],
        f"🎉 <b>Kamu membuka kotak confes!</b>\n"
        f"{n} pesan anonim ditampilkan di bawah. "
        "Kamu bisa balas anonim, lapor, atau blokir per pesan. "
        "Atur kenyamananmu lewat Inbox Confes. 🤍",
        kb, effect="saved",
    )


async def decline_token(client, user: dict, token: str):
    tdoc = await db.targets.find_one({"token": token})
    if not tdoc or tdoc["_id"] != user["_id"]:
        from app.features import start
        return await start.show_main(client, user, note="🤔 Link ini bukan untukmu.")
    await delivery.stop_target(client, user["_id"], "declined")
    await audit.log("confes_declined", actor=user["_id"])
    await screens.show(
        client, user["_id"],
        "🙈 Oke! Kamu <b>tidak akan</b> menerima confes, dan tidak akan dihubungi lagi.\n"
        "Berubah pikiran? Buka 📥 Inbox Confes kapan saja.",
        drafts.home_kb([[InlineKeyboardButton("📥 Inbox Confes", callback_data="ib:home")]]),
    )


@router.cb("c", "cxl")
async def _cb_cancel_pending(client, cq, args, user):
    """Pengirim menarik kembali confes yang masih menunggu dibuka target."""
    conf = await db.confessions.find_one({"_id": utils.oid(args[0])})
    if not conf or conf["sender_id"] != user["_id"]:
        return await cq.answer("Confes ini bukan milikmu.", show_alert=True)
    if conf["status"] != "pending":
        return await cq.answer("Confes ini sudah tidak bisa dibatalkan.", show_alert=True)
    await db.confessions.update_one({"_id": conf["_id"]}, {"$set": {"status": "cancelled"}})
    await audit.log("confes_cancelled", actor=user["_id"], target=conf["target_id"])
    await cq.answer("🗑 Confes dibatalkan.", show_alert=True)
    from app.features import profile
    await profile._cb_myconf(client, cq, [], user)


@router.cb("c", "dec")
async def _cb_decline_inline(client, cq, args, user):
    """Tombol Tolak pada pesan peringatan asisten (inline message)."""
    tdoc = await db.targets.find_one({"token": args[0]})
    if not tdoc or tdoc["_id"] != user["_id"]:
        return await cq.answer("Link ini bukan untukmu.", show_alert=True)
    await delivery.stop_target(client, user["_id"], "declined")
    await audit.log("confes_declined", actor=user["_id"])
    try:
        await client.edit_inline_text(
            cq.inline_message_id,
            "🙈 <b>Confes ditolak.</b> Kamu tidak akan dihubungi lagi.",
            parse_mode=utils.HTML,
        )
    except Exception:
        pass
    await cq.answer("Oke, kamu tidak akan dihubungi lagi. 🙏", show_alert=True)


# ---------------------------------------------------------------- kontrol inbox target

def _onoff(b):
    return "✅" if b else "▫️"


@router.cb("ib", "home")
async def _cb_inbox(client, cq, args, user):
    await _inbox_screen(client, user)


async def _inbox_screen(client, user: dict, note: str = None):
    u = await core_users.get(user["_id"]) or user
    ib = u.get("inbox", {})
    paused = utils.aware(ib.get("paused_until"))
    pause_on = bool(paused and paused > utils.now())
    n_pending = await db.confessions.count_documents(
        {"target_id": u["_id"], "status": "pending"}
    )
    text = (
        "📥 <b>Inbox Confes-mu</b>\n"
        f"Pesan menunggu: <b>{n_pending}</b>\n\n"
        "Atur kenyamananmu — perubahan berlaku seketika:"
    )
    if note:
        text = f"{note}\n\n{text}"
    rows = [
        [InlineKeyboardButton(
            f"{_onoff(ib.get('enabled', True))} Terima Confes", callback_data="ib:t:enabled")],
        [InlineKeyboardButton(
            f"{_onoff(pause_on)} Jeda 24 Jam", callback_data="ib:t:pause")],
        [InlineKeyboardButton(
            f"{_onoff(ib.get('text_only', False))} Hanya Teks (tolak media)", callback_data="ib:t:text")],
    ]
    if n_pending:
        rows.append([InlineKeyboardButton(
            f"📨 Tampilkan {n_pending} Pesan Tertunda", callback_data="ib:flush")])
    rows.append([InlineKeyboardButton("🏠 Menu Utama", callback_data="menu:home")])
    await screens.show(client, user["_id"], text, InlineKeyboardMarkup(rows))


@router.cb("ib", "flush")
async def _cb_inbox_flush(client, cq, args, user):
    u = await core_users.get(user["_id"]) or user
    if u.get("inbox", {}).get("enabled") is False:
        return await cq.answer("Aktifkan 'Terima Confes' dulu ya.", show_alert=True)
    # tandai menerima agar confes berikutnya juga langsung tampil
    await db.targets.update_one({"_id": u["_id"]}, {"$set": {"status": "accepted"}}, upsert=True)
    n = await delivery.flush_pending(client, u["_id"])
    await _inbox_screen(
        client, user,
        note=(f"📨 {n} pesan ditampilkan di bawah." if n else "📭 Tidak ada pesan tertunda."),
    )


@router.cb("ib", "t")
async def _cb_inbox_toggle(client, cq, args, user):
    key = args[0]
    u = await core_users.get(user["_id"]) or user
    ib = u.get("inbox", {})
    note = "👍 Tersimpan."
    if key == "enabled":
        new = not ib.get("enabled", True)
        await db.users.update_one({"_id": u["_id"]}, {"$set": {"inbox.enabled": new}})
        if new:
            await db.targets.update_one(
                {"_id": u["_id"]}, {"$set": {"status": "accepted"}}, upsert=True
            )
            n = await delivery.flush_pending(client, u["_id"])
            note = f"💌 Confes diaktifkan — {n} pesan tertunda ditampilkan." if n else "💌 Confes diaktifkan."
        else:
            note = "😶 Confes dimatikan untukmu. Tidak ada lagi yang masuk."
    elif key == "pause":
        paused = utils.aware(ib.get("paused_until"))
        if paused and paused > utils.now():
            await db.users.update_one({"_id": u["_id"]}, {"$set": {"inbox.paused_until": None}})
            n = await delivery.flush_pending(client, u["_id"])
            note = f"▶️ Jeda dicabut — {n} pesan tertunda masuk." if n else "▶️ Jeda dicabut."
        else:
            until = utils.now() + timedelta(hours=24)
            await db.users.update_one({"_id": u["_id"]}, {"$set": {"inbox.paused_until": until}})
            note = "⏸️ Confes dijeda 24 jam."
    elif key == "text":
        await db.users.update_one(
            {"_id": u["_id"]}, {"$set": {"inbox.text_only": not ib.get("text_only", False)}}
        )
    await audit.log("inbox_pref", actor=u["_id"], key=key)
    await _inbox_screen(client, user, note=note)
