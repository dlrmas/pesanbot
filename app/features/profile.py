"""Profil = dashboard pengguna: statistik, saldo, lencana, referral, redeem, riwayat."""
from datetime import timedelta

from pymongo.errors import DuplicateKeyError
from pyrogram.types import InlineKeyboardMarkup

from app import clients, db
from app.core import audit, content as content_mod
from app.core import router, screens, ui, utils, wallet
from app.core import users as core_users

_STATUS_ICON = {
    "sent": "✅", "review": "🕵️", "rejected": "🚫", "failed": "❌",
    "cancelled": "🗑", "draft": "📝", "pending": "⏳", "delivered": "✅", "stopped": "😶",
}


@router.cb("p", "home")
async def _cb_home(client, cq, args, user):
    await show(client, user)


@router.command("profil")
async def _cmd_profil(client, m, user, payload):
    await screens.drop(client, m)
    await show(client, user)


async def show(client, user: dict, note: str = None):
    u = await core_users.get(user["_id"]) or user
    s = await db.get_settings()
    w = wallet.summary(u, s)
    st = u.get("stats", {})
    badges = core_users.badges(u)

    saldo = [
        ui.field("🔋 Poin hari ini", f"{w['points_left']}/{w['points_quota']}") + " · reset 00:00 WIB",
        ui.field("🪙 Coin", f"{w['coins']} tersimpan"),
    ]
    if w["vip"]:
        saldo.append(f"👑 VIP aktif s/d {utils.fmt_dt(w['vip_until'])}")
    lines = [
        f"👤 <b>{utils.esc(u.get('name', 'Anon'))}</b> · {core_users.rank(u)}",
        f"🆔 <code>{u['_id']}</code> · bergabung {utils.fmt_dt(u.get('joined_at'))}",
        "",
        ui.quote("\n".join(saldo)),
        f"📊 Menfes <b>{st.get('menfes', 0)}</b> · Confes <b>{st.get('confes', 0)}</b>"
        f" · Balasan <b>{st.get('replies', 0)}</b>",
        f"🤝 Referral berhasil: <b>{u.get('referrals', 0)}</b>",
    ]
    if badges:
        lines.append(ui.quote("🏅 " + "\n🏅 ".join(badges), expandable=len(badges) > 2))
    if note:
        lines.insert(0, note + "\n")

    rows = [
        [
            ui.primary("✉️ Menfes", "m:new"),
            ui.primary("💌 Confes", "c:new"),
        ],
        [
            ui.btn("🏆 Referral", "p:ref"),
            ui.btn("🎁 Redeem", "p:redeem"),
        ],
        [
            ui.btn("🗂 Riwayat", "p:hist"),
            ui.btn("📤 Confes Saya", "p:myconf"),
        ],
        [
            ui.btn("📥 Inbox", "ib:home"),
            ui.btn("🏠 Menu Utama", "menu:home"),
        ],
    ]
    await screens.show(client, user["_id"], "\n".join(lines), InlineKeyboardMarkup(rows))


@router.cb("p", "myconf")
async def _cb_myconf(client, cq, args, user):
    """Daftar confes yang sudah dikirim tapi masih menunggu dibuka target —
    pengirim bisa menariknya kembali selagi pending."""
    uid = user["_id"]
    lines = ["📤 <b>Confes-mu yang menunggu dibuka</b>", ""]
    rows = []
    n = 0
    cursor = db.confessions.find(
        {"sender_id": uid, "status": "pending"}
    ).sort("created_at", -1).limit(10)
    async for c in cursor:
        body = content_mod.body(c.get("content") or {}) or "(media)"
        lines.append(f"⏳ → <b>{utils.esc(c.get('target_name', '?'))}</b>: {utils.esc(utils.take(body, 28))}")
        rows.append([ui.danger(
            f"🗑 Batalkan → {utils.take(c.get('target_name', '?'), 14)}", f"c:cxl:{c['_id']}",
        )])
        n += 1
    if not n:
        lines.append("<i>Tidak ada confes yang menunggu.</i>")
    rows.append([ui.btn("👤 Profil", "p:home")])
    await screens.show(client, uid, "\n".join(lines), InlineKeyboardMarkup(rows))


# ---------------------------------------------------------------- referral

@router.cb("p", "ref")
async def _cb_ref(client, cq, args, user):
    link = f"https://t.me/{clients.bot_username}?start=ref_{user['_id']}"
    text = (
        "🏆 <b>Referral</b>\n"
        "<i>Tiap teman yang join = ❤️ +3 coin untukmu, +1 untuk dia.</i>\n\n"
        + ui.quote(
            f"🎫 Kode: <code>ref_{user['_id']}</code>\n"
            f"🔗 {link}\n"
            f"🤝 Berhasil mengajak: <b>{user.get('referrals', 0)}</b> orang"
        )
        + "\n👇 Salin sekali ketuk, atau bagikan kartunya langsung:"
    )
    rows = [
        [ui.copybtn("📋 Salin Link", link)],
        [ui.sharebtn("📤 Bagikan Kartu Ajakan")],
        [ui.btn("⌨️ Masukkan Kode Teman", "p:refin")],
        [ui.btn("👤 Profil", "p:home")],
    ]
    await screens.show(client, user["_id"], text, InlineKeyboardMarkup(rows))


@router.cb("p", "refin")
async def _cb_refin(client, cq, args, user):
    if user.get("referred_by"):
        return await cq.answer("Kamu sudah pernah memakai kode referral.", show_alert=True)
    await router.set_state(user["_id"], "profile.refcode")
    await screens.ask(
        client, user["_id"],
        "⌨️ <b>Kode Referral Teman</b>\nBalas dengan kodenya, contoh: <code>ref_123456789</code>",
        placeholder="ref_123456789",
    )


@router.state("profile.refcode")
async def _st_refcode(client, m, user, st):
    await screens.drop(client, m)
    await router.clear_state(user["_id"])
    note = await apply_referral(client, user, (m.text or "").strip())
    await show(client, user, note=note)


async def apply_referral(client, user: dict, code: str) -> str:
    """Klaim kode referral — sekali seumur hidup, anti dobel via ledger ref unik."""
    uid = user["_id"]
    inviter_id = utils.parse_int(code.lower().removeprefix("ref_"))
    if not inviter_id:
        return "🤔 Kode referral tidak valid."
    if inviter_id == uid:
        return "😅 Tidak bisa memakai kode referral sendiri."
    fresh = await core_users.get(uid)
    if fresh.get("referred_by"):
        return "ℹ️ Kamu sudah pernah memakai kode referral."
    inviter = await core_users.get(inviter_id)
    if not inviter or inviter.get("banned"):
        return "🤔 Pemilik kode tidak ditemukan."

    if not await wallet.add_coins(uid, 1, "bonus pakai referral", ref=f"ref_in:{uid}"):
        return "ℹ️ Kamu sudah pernah memakai kode referral."
    await db.users.update_one({"_id": uid}, {"$set": {"referred_by": inviter_id}})
    await wallet.add_coins(inviter_id, 3, "bonus mengajak teman", ref=f"ref_out:{uid}")
    await db.users.update_one({"_id": inviter_id}, {"$inc": {"referrals": 1}})
    await audit.log("referral", actor=uid, target=inviter_id)
    await screens.notify(
        client, inviter_id,
        "❤️ <b>+3 coin!</b> Temanmu bergabung lewat link referralmu. Makasih sudah menyebarkan 🤍",
        effect="reward",
    )
    return "🎉 Kode diterima — <b>+1 coin</b> untukmu!"


# ---------------------------------------------------------------- redeem voucher

@router.cb("p", "redeem")
async def _cb_redeem(client, cq, args, user):
    await router.set_state(user["_id"], "profile.voucher")
    await screens.ask(
        client, user["_id"],
        "🎁 <b>Redeem Voucher</b>\nBalas dengan kode vouchermu.",
        placeholder="KODEVOUCHER",
    )


@router.state("profile.voucher")
async def _st_voucher(client, m, user, st):
    await screens.drop(client, m)
    await router.clear_state(user["_id"])
    note, eff = await claim_voucher(user, (m.text or "").strip().upper())
    u = await core_users.get(user["_id"]) or user
    if eff:
        await screens.notify(client, user["_id"], note, effect=eff)
        return await show(client, u)
    await show(client, u, note=note)


async def claim_voucher(user: dict, code: str) -> tuple[str, str | None]:
    uid = user["_id"]
    v = await db.vouchers.find_one({"_id": code})
    if not v or not v.get("active"):
        return "🤔 Kode voucher tidak ditemukan / sudah nonaktif.", None
    exp = utils.aware(v.get("expires_at"))
    if exp and exp < utils.now():
        return "⌛ Voucher sudah kedaluwarsa.", None
    if v.get("audience") == "new":
        joined = utils.aware(user.get("joined_at"))
        if not joined or (utils.now() - joined).days > 7:
            return "🙏 Voucher ini khusus pengguna baru.", None
    only = v.get("only_user")
    if only and only != uid:
        return "🙏 Voucher ini bukan untukmu.", None

    try:
        await db.voucher_claims.insert_one({"code": code, "user_id": uid, "at": utils.now()})
    except DuplicateKeyError:
        return "ℹ️ Kamu sudah pernah klaim voucher ini.", None

    q = {"_id": code, "active": True}
    if v.get("max_claims", 0) > 0:
        q["$expr"] = {"$lt": ["$claims", "$max_claims"]}
    r = await db.vouchers.update_one(q, {"$inc": {"claims": 1}})
    if r.modified_count == 0:
        await db.voucher_claims.delete_one({"code": code, "user_id": uid})
        return "😢 Yah, kuota voucher ini sudah habis.", None

    amount = v.get("amount", 0)
    vtype = v.get("type")
    await audit.log("voucher_claim", actor=uid, target=code, vtype=vtype, amount=amount)
    if vtype == "coin":
        await wallet.add_coins(uid, amount, f"voucher {code}", ref=f"vc:{code}:{uid}")
        return f"❤️ <b>+{amount} coin</b> dari voucher <code>{code}</code>!", "reward"
    if vtype == "vip":
        until = await wallet.grant_vip(uid, amount, f"voucher {code}")
        return f"👑 <b>VIP aktif</b> s/d {utils.fmt_dt(until)} 🎉", "vip"
    if vtype == "points":
        await wallet.grant_points(uid, amount, f"voucher {code}")
        return f"⚡ <b>+{amount} poin</b> untuk hari ini!", "reward"
    if vtype == "nocd":
        until = utils.now() + timedelta(hours=amount)
        await db.users.update_one({"_id": uid}, {"$set": {"nocd_until": until}})
        return f"🚀 <b>Bebas cooldown</b> selama {amount} jam!", "success"
    return "🤷 Tipe voucher tidak dikenal — hubungi admin.", None


# ---------------------------------------------------------------- riwayat

@router.cb("p", "hist")
async def _cb_hist(client, cq, args, user):
    uid = user["_id"]
    lines = ["🗂 <b>Riwayat Terakhir</b>", ""]
    # hanya menfes di sini: confes punya daftarnya sendiri di bawah, dan balasan
    # anonim adalah bagian dari percakapan (bukan kiriman berdiri sendiri) — jadi
    # tidak ditampilkan dobel.
    drafts_cur = db.drafts.find(
        {"user_id": uid, "kind": "menfes", "status": {"$ne": "cancelled"}}
    ).sort("created_at", -1).limit(6)
    any_row = False
    async for d in drafts_cur:
        ic = _STATUS_ICON.get(d["status"], "•")
        body = (d.get("content") or {}).get("text") or (d.get("content") or {}).get("caption") or "(media)"
        lines.append(
            f"{ic} <b>menfes</b> · {utils.esc(utils.take(body, 32))} · {utils.fmt_dt(d['created_at'])}"
        )
        any_row = True
    conf_cur = db.confessions.find({"sender_id": uid}).sort("created_at", -1).limit(4)
    async for c in conf_cur:
        ic = _STATUS_ICON.get(c["status"], "•")
        lines.append(
            f"{ic} <b>confes</b> → {utils.esc(c.get('target_name', '?'))} · {c['status']}"
        )
        any_row = True
    if not any_row:
        lines.append("Belum ada aktivitas. Mulai dari menu utama yuk! ✨")
    kb = InlineKeyboardMarkup([[ui.btn("👤 Profil", "p:home")]])
    await screens.show(client, user["_id"], "\n".join(lines), kb)
