"""Admin: kelola pengguna (cari, ban, warn, coin, cooldown, VIP) + menu VIP.

Moderator hanya boleh: lihat, ban/unban, peringatkan.
Coin, cooldown khusus, dan VIP hanya Owner.
"""
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app import db
from app.core import audit, router, screens, ui, utils, wallet
from app.core import users as core_users
from app.features.admin.panel import BACK as _BACK


async def _ask_find(client, uid: int, note: str = None):
    """Prompt cari pengguna — ketik ID/@username ATAU pilih dari kontak
    (penyelamat saat admin lupa ID & username)."""
    await router.set_state(uid, "admin.user_find")
    picker = ui.contact_picker("👤 Pilih dari Kontak", button_id=9)
    text = (
        "👥 <b>Cari Pengguna</b>\n"
        "Ketuk <b>👤 Pilih dari Kontak</b>, atau balas dengan ID / @username."
    )
    if note:
        text = f"{note}\n\n{text}"
    await screens.ask(client, uid, text, placeholder="@username / 123456789", choices=[[picker]])


async def _banned_screen(client, user, note: str = None):
    lines = [ui.title("Daftar Pengguna Diban", "🚫"), ""]
    rows = []
    n = 0
    async for u in db.users.find({"banned": True}).sort("_id", 1).limit(20):
        lines.append(f"• {utils.esc(u.get('name', '?'))} <code>{u['_id']}</code>")
        rows.append([InlineKeyboardButton(
            f"👤 {utils.take(u.get('name', '?'), 20)}", callback_data=f"a:us:v:{u['_id']}",
        )])
        n += 1
    if not n:
        lines.append(ui.muted("Tidak ada pengguna yang diban. 🎉"))
    if note:
        lines.insert(0, note + "\n")
    rows.append([
        InlineKeyboardButton("📊 Stats & User", callback_data="a:st"),
        InlineKeyboardButton("🛠 Panel", callback_data="a:home"),
    ])
    await screens.show(client, user["_id"], "\n".join(lines), InlineKeyboardMarkup(rows))


@router.cb("a", "us", admin=True)
async def _cb_users(client, cq, args, user):
    sub = args[0] if args else "home"
    role = await core_users.get_role(user["_id"])

    if sub == "home":
        return await _ask_find(client, user["_id"])
    if sub == "banned":
        return await _banned_screen(client, user)
    if sub == "v":
        target = await core_users.get(utils.parse_int(args[1]))
        if not target:
            return await cq.answer("Pengguna tidak ditemukan.", show_alert=True)
        return await show_card(client, user, target)

    uid = utils.parse_int(args[1]) if len(args) > 1 else None
    target = await core_users.get(uid) if uid else None
    if not target:
        return await cq.answer("Pengguna tidak ditemukan.", show_alert=True)

    if sub == "ban":
        if len(args) < 3 or args[2] != "yes":  # konfirmasi kedua aksi berbahaya
            verb = "Unban" if target.get("banned") else "Ban"
            kb = InlineKeyboardMarkup([
                [ui.danger(f"🚫 Yakin, {verb}", f"a:us:ban:{uid}:yes")],
                [ui.btn("◀️ Batal", f"a:us:v:{uid}")],
            ])
            return await screens.show(
                client, user["_id"],
                f"{verb} <b>{utils.esc(target.get('name'))}</b> (<code>{uid}</code>)?", kb,
            )
        new = not target.get("banned")
        await db.users.update_one({"_id": uid}, {"$set": {"banned": new}})
        await audit.log("ban" if new else "unban", actor=user["_id"], target=uid)
        if not new:
            await screens.notify(client, uid, "♻️ Akses bot-mu dipulihkan. Jaga sikap ya 🤝")
        return await show_card(client, user, await core_users.get(uid),
                               note="🚫 Diban." if new else "♻️ Di-unban.")

    if sub == "warn":
        await db.users.update_one({"_id": uid}, {"$inc": {"warnings": 1}})
        await audit.log("warn", actor=user["_id"], target=uid)
        await screens.notify(
            client, uid,
            "⚠️ <b>Peringatan dari admin.</b> Patuh aturan ya — pelanggaran berikutnya bisa berujung ban.",
        )
        return await show_card(client, user, await core_users.get(uid), note="⚠️ Peringatan terkirim.")

    if role != "owner":
        return await cq.answer("🔒 Aksi ini khusus Owner.", show_alert=True)

    if sub == "coin":
        sign = args[2] if len(args) > 2 else "plus"
        await router.set_state(user["_id"], "admin.coin_amt", uid=uid, sign=sign)
        verb = "ditambahkan ke" if sign == "plus" else "ditarik dari"
        return await screens.ask(
            client, user["_id"],
            f"🪙 Balas dengan jumlah coin yang {verb} <b>{utils.esc(target.get('name'))}</b>.",
            placeholder="jumlah coin",
        )
    if sub == "cd":
        await router.set_state(user["_id"], "admin.cd_secs", uid=uid)
        return await screens.ask(
            client, user["_id"],
            f"⏱ Balas dengan cooldown khusus (detik) untuk <b>{utils.esc(target.get('name'))}</b>.\n"
            "Balas <code>0</code> untuk menghapus cooldown khusus.",
            placeholder="detik (0 = hapus)",
            choices=[["0", "300", "3600"]],
        )
    if sub == "vipadd":
        until = await wallet.grant_vip(uid, 30, "VIP dari admin", by=user["_id"])
        await audit.log("vip_add", actor=user["_id"], target=uid)
        await screens.notify(
            client, uid, f"👑 <b>Kamu sekarang VIP!</b> Aktif s/d {utils.fmt_dt(until)} 🎉",
            effect="vip",
        )
        return await show_card(client, user, await core_users.get(uid), note="👑 VIP +30 hari.")
    if sub == "vipdel":
        await wallet.revoke_vip(uid, by=user["_id"])
        await audit.log("vip_del", actor=user["_id"], target=uid)
        return await show_card(client, user, await core_users.get(uid), note="👑 VIP dicabut.")

    if sub == "freeze":
        await router.set_state(user["_id"], "admin.freeze_min", uid=uid)
        return await screens.ask(
            client, user["_id"],
            f"❄️ Bekukan <b>{utils.esc(target.get('name'))}</b> berapa menit?\n"
            "Selama beku, semua pengiriman (menfes/confes/balasan) ditahan.",
            placeholder="menit",
            choices=[["30", "60", "1440"]],
        )
    if sub == "unfreeze":
        await db.users.update_one({"_id": uid}, {"$set": {"cooldown_until": None}})
        await audit.log("unfreeze", actor=user["_id"], target=uid)
        await screens.notify(client, uid, "♨️ Pembekuan akunmu dicairkan admin. Kamu bisa mengirim lagi.")
        return await show_card(client, user, await core_users.get(uid), note="♨️ Pembekuan dicairkan.")


async def show_card(client, admin: dict, target: dict, note: str = None):
    uid = target["_id"]
    s = await db.get_settings()
    w = wallet.summary(target, s)
    score, signals = audit.risk(target)
    st = target.get("stats", {})

    frozen = utils.aware(target.get("cooldown_until"))
    is_frozen = bool(frozen and frozen > utils.now())

    lines = [
        f"👤 <b>{utils.esc(target.get('name'))}</b>"
        + (f" (@{target['username']})" if target.get("username") else ""),
        f"🆔 <code>{uid}</code> · join {utils.fmt_dt(target.get('joined_at'))}",
        f"{'🚫 BANNED' if target.get('banned') else '✅ Aktif'} · {core_users.rank(target)}",
        "─────────────",
        f"⚡ {w['points_left']}/{w['points_quota']} poin · 🪙 {w['coins']} coin"
        + (f" · 👑 VIP s/d {utils.fmt_dt(w['vip_until'])}" if w["vip"] else ""),
        f"📊 menfes {st.get('menfes', 0)} · confes {st.get('confes', 0)} · balasan {st.get('replies', 0)}",
        f"⚠️ Peringatan: {target.get('warnings', 0)}"
        + (f" · ⏱ cooldown khusus {target['custom_cooldown']} dtk" if target.get("custom_cooldown") else ""),
        f"🎯 Risiko: {audit.risk_badge(score)}",
    ]
    if is_frozen:
        lines.append(f"❄️ <b>Dibekukan</b> s/d {utils.fmt_dt(frozen)}")
    lines += [f"   {sig}" for sig in signals]
    if note:
        lines.insert(0, note + "\n")

    freeze_btn = (
        InlineKeyboardButton("♨️ Cairkan", callback_data=f"a:us:unfreeze:{uid}")
        if is_frozen else
        InlineKeyboardButton("❄️ Bekukan", callback_data=f"a:us:freeze:{uid}")
    )
    rows = [
        [
            InlineKeyboardButton("♻️ Unban" if target.get("banned") else "🚫 Ban",
                                 callback_data=f"a:us:ban:{uid}"),
            InlineKeyboardButton("⚠️ Peringatkan", callback_data=f"a:us:warn:{uid}"),
        ],
        [
            InlineKeyboardButton("🪙 +Coin", callback_data=f"a:us:coin:{uid}:plus"),
            InlineKeyboardButton("🪙 −Coin", callback_data=f"a:us:coin:{uid}:minus"),
            InlineKeyboardButton("⏱ Cooldown", callback_data=f"a:us:cd:{uid}"),
        ],
        [
            InlineKeyboardButton("👑 VIP −", callback_data=f"a:us:vipdel:{uid}")
            if core_users.is_vip(target) else
            InlineKeyboardButton("👑 VIP +30d", callback_data=f"a:us:vipadd:{uid}"),
            freeze_btn,
        ],
        [
            InlineKeyboardButton("🔄 Segarkan", callback_data=f"a:us:v:{uid}"),
            InlineKeyboardButton("🔎 Cari Lagi", callback_data="a:us"),
            _BACK[0],
        ],
    ]
    await screens.show(client, admin["_id"], "\n".join(lines), InlineKeyboardMarkup(rows))


@router.state("admin.user_find")
async def _st_find(client, m, user, st):
    await screens.drop(client, m)
    if m.users_shared and m.users_shared.users:  # via tombol Pilih dari Kontak
        tid = getattr(m.users_shared.users[0], "id", None)
        target = await core_users.get(tid) if tid else None
        if not target:
            return await _ask_find(
                client, user["_id"],
                note="ℹ️ Kontak itu belum pernah memakai bot, jadi belum ada datanya.",
            )
    else:
        target = await core_users.resolve_ref(m.text or "")
        if not target:
            return await _ask_find(client, user["_id"], note="🔍 Tidak ketemu di database.")
    await router.clear_state(user["_id"])
    await show_card(client, user, target)


@router.state("admin.coin_amt")
async def _st_coin(client, m, user, st):
    await screens.drop(client, m)
    n = utils.parse_int(m.text)
    uid, sign = st["data"]["uid"], st["data"]["sign"]
    if not n or n <= 0:
        return await screens.ask(
            client, user["_id"],
            "⚠️ Masukkan jumlah coin yang valid (angka lebih dari 0).",
            placeholder="jumlah coin",
        )
    await router.clear_state(user["_id"])
    if sign == "plus":
        await wallet.add_coins(uid, n, "dari admin", by=user["_id"])
        await screens.notify(client, uid, f"❤️ <b>+{n} coin</b> hadiah dari admin!", effect="gift")
        note = f"✅ +{n} coin."
    else:
        ok = await wallet.take_coins(uid, n, "ditarik admin", by=user["_id"])
        note = f"✅ −{n} coin." if ok else "⚠️ Saldo pengguna tidak cukup."
    target = await core_users.get(uid)
    await show_card(client, user, target, note=note)


@router.state("admin.cd_secs")
async def _st_cd(client, m, user, st):
    await screens.drop(client, m)
    n = utils.parse_int(m.text)
    uid = st["data"]["uid"]
    if n is None or n < 0:
        return await screens.ask(
            client, user["_id"],
            "⚠️ Masukkan angka detik yang valid (0 atau lebih).",
            placeholder="detik (0 = hapus)",
            choices=[["0", "300", "3600"]],
        )
    await router.clear_state(user["_id"])
    await db.users.update_one({"_id": uid}, {"$set": {"custom_cooldown": n or None}})
    await audit.log("custom_cooldown", actor=user["_id"], target=uid, secs=n)
    await show_card(client, user, await core_users.get(uid),
                    note="✅ Cooldown khusus dihapus." if n == 0 else f"✅ Cooldown {n} dtk.")


@router.state("admin.freeze_min")
async def _st_freeze(client, m, user, st):
    await screens.drop(client, m)
    n = utils.parse_int(m.text)
    uid = st["data"]["uid"]
    if not n or n <= 0:
        return await screens.ask(
            client, user["_id"],
            "⚠️ Masukkan menit yang valid (lebih dari 0).",
            placeholder="menit",
            choices=[["30", "60", "1440"]],
        )
    await router.clear_state(user["_id"])
    from datetime import timedelta
    until = utils.now() + timedelta(minutes=n)
    await db.users.update_one({"_id": uid}, {"$set": {"cooldown_until": until}})
    await audit.log("freeze", actor=user["_id"], target=uid, mins=n)
    await screens.notify(
        client, uid,
        f"❄️ Akunmu dibekukan sementara oleh admin (±{utils.left_str(n * 60)}). "
        "Pengiriman ditahan dulu ya.",
    )
    await show_card(client, user, await core_users.get(uid), note=f"❄️ Dibekukan {n} mnt.")


# ================================================================ menu VIP (Owner)

@router.cb("a", "vip", owner=True)
async def _cb_vip(client, cq, args, user):
    sub = args[0] if args else "home"
    if sub == "add":
        await router.set_state(user["_id"], "admin.vip_add")
        return await screens.ask(
            client, user["_id"],
            "👑 <b>Tambah VIP</b>\nBalas dengan: <code>ID jumlah_hari</code>\n"
            "Contoh: <code>123456789 30</code>",
            placeholder="123456789 30",
        )
    await _vip_screen(client, user)


async def _vip_screen(client, user, note: str = None):
    lines = ["👑 <b>Pengguna VIP Aktif</b>", ""]
    cur = db.users.find({"vip_until": {"$gt": utils.now()}}).sort("vip_until", 1).limit(20)
    n = 0
    async for u in cur:
        lines.append(f"• {utils.esc(u.get('name'))} <code>{u['_id']}</code> — s/d {utils.fmt_dt(u['vip_until'])}")
        n += 1
    if not n:
        lines.append("<i>Belum ada VIP.</i>")
    lines.append("\nCabut VIP lewat menu 👥 Pengguna → kartu pengguna.")
    if note:
        lines.insert(0, note + "\n")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Tambah VIP", callback_data="a:vip:add")],
        _BACK,
    ])
    await screens.show(client, user["_id"], "\n".join(lines), kb)


@router.state("admin.vip_add")
async def _st_vip_add(client, m, user, st):
    await screens.drop(client, m)
    toks = (m.text or "").split()
    uid = utils.parse_int(toks[0]) if toks else None
    days = utils.parse_int(toks[1]) if len(toks) > 1 else 30
    target = await core_users.get(uid) if uid else None
    if not target or not days or days <= 0:
        hint = ("🔍 Pengguna tidak ditemukan di database."
                if uid and not target else
                "⚠️ Format salah. Kirim: <code>ID jumlah_hari</code> (hari lebih dari 0).")
        return await screens.ask(
            client, user["_id"],
            f"{hint}\nContoh: <code>123456789 30</code>",
            placeholder="123456789 30",
        )
    await router.clear_state(user["_id"])
    until = await wallet.grant_vip(uid, days, "VIP dari admin", by=user["_id"])
    await audit.log("vip_add", actor=user["_id"], target=uid, days=days)
    await screens.notify(
        client, uid, f"👑 <b>Kamu sekarang VIP!</b> Aktif s/d {utils.fmt_dt(until)} 🎉", effect="vip",
    )
    await _vip_screen(client, user, note=f"✅ VIP {days} hari untuk {utils.esc(target.get('name'))}.")
