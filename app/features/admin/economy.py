"""Admin (Owner): voucher — coin, VIP, poin tambahan, bebas cooldown."""
from datetime import timedelta

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app import db
from app.core import audit, router, screens, ui, utils
from app.core import users as core_users
from app.features.admin.panel import BACK as _BACK

_TYPES = {
    "coin": ("🪙 Coin", "jumlah coin"),
    "vip": ("👑 VIP", "jumlah hari VIP"),
    "points": ("⚡ Poin Tambahan", "jumlah poin (untuk hari klaim)"),
    "nocd": ("🚀 Bebas Cooldown", "durasi jam bebas cooldown"),
}


@router.cb("a", "vc", owner=True)
async def _cb_voucher(client, cq, args, user):
    sub = args[0] if args else "home"

    if sub == "new":
        rows = [[InlineKeyboardButton(label, callback_data=f"a:vc:t:{key}")]
                for key, (label, _) in _TYPES.items()]
        rows.append([InlineKeyboardButton("◀️ Batal", callback_data="a:vc")])
        return await screens.show(
            client, user["_id"], "🎟 <b>Voucher Baru</b>\nPilih jenis hadiah:",
            InlineKeyboardMarkup(rows),
        )

    if sub == "t":
        vtype = args[1]
        await router.set_state(user["_id"], "admin.vc_amount", vtype=vtype)
        return await screens.ask(
            client, user["_id"],
            f"🔢 Balas dengan <b>{_TYPES[vtype][1]}</b>.",
            placeholder=_TYPES[vtype][1],
        )

    if sub == "aud":
        st = await router.get_state(user["_id"])
        if not st or st.get("name") != "admin.vc_wait_aud":
            return await cq.answer("Sesi pembuatan voucher sudah berakhir.", show_alert=True)
        d = st["data"]
        if args[1] == "user":  # voucher personal → minta target dulu
            await router.set_state(user["_id"], "admin.vc_user", **d)
            return await screens.ask(
                client, user["_id"],
                "👤 <b>Voucher Personal</b>\nBalas dengan ID/@username penerima.",
                placeholder="@username / 123456789",
            )
        await router.clear_state(user["_id"])
        code = utils.gen_code(8)
        expires = (
            utils.now() + timedelta(days=d["days"]) if d["days"] > 0 else None
        )
        await db.vouchers.insert_one({
            "_id": code, "type": d["vtype"], "amount": d["amount"],
            "max_claims": d["claims"], "claims": 0, "audience": args[1],
            "expires_at": expires, "active": True,
            "created_at": utils.now(), "by": user["_id"],
        })
        await audit.log("voucher_create", actor=user["_id"], target=code, vtype=d["vtype"])
        label = _TYPES[d["vtype"]][0]
        return await _vc_screen(
            client, user,
            note=(f"🎉 Voucher <code>{code}</code> dibuat!\n"
                  f"{label} ×{d['amount']} · kuota {d['claims'] or '∞'} · "
                  f"{'pengguna baru' if args[1] == 'new' else 'semua pengguna'}"),
        )

    if sub == "v":
        code = args[1]
        v = await db.vouchers.find_one({"_id": code})
        if not v:
            return await cq.answer("Voucher tidak ditemukan.", show_alert=True)
        n_claims = await db.voucher_claims.count_documents({"code": code})
        label = _TYPES.get(v["type"], (v["type"], ""))[0]
        if v.get("only_user"):
            audiens = f"personal → <code>{v['only_user']}</code>"
        elif v.get("audience") == "new":
            audiens = "pengguna baru"
        else:
            audiens = "semua"
        text = (
            f"🎟 <b>{code}</b> {'🟢' if v.get('active') else '🔴 nonaktif'}\n"
            f"{label} ×{v['amount']} · klaim {n_claims}/{v.get('max_claims') or '∞'}\n"
            f"Audiens: {audiens}\n"
            f"Kedaluwarsa: {utils.fmt_dt(v.get('expires_at')) if v.get('expires_at') else 'tidak ada'}"
        )
        kb = InlineKeyboardMarkup([
            [ui.copybtn("📋 Salin Kode", code)],
            [
                ui.btn("🔁 Aktif/Nonaktif", f"a:vc:tg:{code}"),
                ui.danger("🗑 Hapus", f"a:vc:del:{code}"),
            ],
            [ui.btn("◀️ Daftar Voucher", "a:vc")],
        ])
        return await screens.show(client, user["_id"], text, kb)

    if sub == "tg":
        v = await db.vouchers.find_one({"_id": args[1]})
        if v:
            await db.vouchers.update_one({"_id": args[1]}, {"$set": {"active": not v.get("active")}})
            await audit.log("voucher_toggle", actor=user["_id"], target=args[1])
        return await _vc_screen(client, user, note="🔁 Status voucher diubah.")

    if sub == "del":
        if len(args) < 3 or args[2] != "yes":  # konfirmasi kedua
            kb = InlineKeyboardMarkup([
                [ui.danger("🗑 Yakin, hapus", f"a:vc:del:{args[1]}:yes")],
                [ui.btn("◀️ Batal", f"a:vc:v:{args[1]}")],
            ])
            return await screens.show(
                client, user["_id"], f"Hapus voucher <code>{args[1]}</code>?", kb,
            )
        await db.vouchers.delete_one({"_id": args[1]})
        await audit.log("voucher_delete", actor=user["_id"], target=args[1])
        return await _vc_screen(client, user, note="🗑 Voucher dihapus.")

    await _vc_screen(client, user)


async def _vc_screen(client, user, note: str = None):
    rows = []
    async for v in db.vouchers.find({}).sort("created_at", -1).limit(10):
        ic = "🟢" if v.get("active") else "🔴"
        label = _TYPES.get(v["type"], (v["type"], ""))[0]
        rows.append([InlineKeyboardButton(
            f"{ic} {v['_id']} · {label} ×{v['amount']} · {v.get('claims', 0)}/{v.get('max_claims') or '∞'}",
            callback_data=f"a:vc:v:{v['_id']}",
        )])
    text = "🎟 <b>Voucher</b>\nKetuk untuk kelola." if rows else "🎟 <b>Voucher</b>\n<i>Belum ada voucher.</i>"
    if note:
        text = f"{note}\n\n{text}"
    rows.append([InlineKeyboardButton("➕ Buat Voucher", callback_data="a:vc:new")])
    rows.append(_BACK)
    await screens.show(client, user["_id"], text, InlineKeyboardMarkup(rows))


# ---- wizard input angka ----

@router.state("admin.vc_amount")
async def _st_amount(client, m, user, st):
    await screens.drop(client, m)
    n = utils.parse_int(m.text)
    if not n or n <= 0:
        label = _TYPES.get(st["data"]["vtype"], ("", "jumlah"))[1]
        return await screens.ask(
            client, user["_id"],
            f"⚠️ Masukkan {label} (angka lebih dari 0).",
            placeholder=label,
        )
    await router.set_state(user["_id"], "admin.vc_claims", vtype=st["data"]["vtype"], amount=n)
    await screens.ask(
        client, user["_id"],
        "🎫 Batas total klaim? Balas angka (<code>0</code> = tanpa batas).",
        placeholder="0 = tanpa batas",
        choices=[["0", "10", "50", "100"]],
    )


@router.state("admin.vc_claims")
async def _st_claims(client, m, user, st):
    await screens.drop(client, m)
    n = utils.parse_int(m.text)
    if n is None or n < 0:
        return await screens.ask(
            client, user["_id"],
            "⚠️ Masukkan angka (0 = tanpa batas).",
            placeholder="0 = tanpa batas",
            choices=[["0", "10", "50", "100"]],
        )
    d = st["data"]
    await router.set_state(user["_id"], "admin.vc_days", vtype=d["vtype"], amount=d["amount"], claims=n)
    await screens.ask(
        client, user["_id"],
        "📅 Masa berlaku berapa hari? Balas angka (<code>0</code> = selamanya).",
        placeholder="0 = selamanya",
        choices=[["0", "7", "30"]],
    )


@router.state("admin.vc_days")
async def _st_days(client, m, user, st):
    await screens.drop(client, m)
    n = utils.parse_int(m.text)
    if n is None or n < 0:
        return await screens.ask(
            client, user["_id"],
            "⚠️ Masukkan angka hari (0 = selamanya).",
            placeholder="0 = selamanya",
            choices=[["0", "7", "30"]],
        )
    d = st["data"]
    await router.set_state(
        user["_id"], "admin.vc_wait_aud",
        vtype=d["vtype"], amount=d["amount"], claims=d["claims"], days=n,
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌍 Semua Pengguna", callback_data="a:vc:aud:all"),
            InlineKeyboardButton("🌱 Pengguna Baru", callback_data="a:vc:aud:new"),
        ],
        [InlineKeyboardButton("👤 User Tertentu", callback_data="a:vc:aud:user")],
        [InlineKeyboardButton("◀️ Batal", callback_data="a:vc")],
    ])
    await screens.show(client, user["_id"], "👥 Siapa yang boleh klaim?", kb)


@router.state("admin.vc_user")
async def _st_vc_user(client, m, user, st):
    await screens.drop(client, m)
    target = await core_users.resolve_ref(m.text or "")
    if not target:
        return await screens.ask(
            client, user["_id"],
            "🔍 Tidak ketemu di database. Balas ID/@username lain.",
            placeholder="@username / 123456789",
        )
    d = st["data"]
    await router.clear_state(user["_id"])
    code = utils.gen_code(8)
    expires = utils.now() + timedelta(days=d["days"]) if d["days"] > 0 else None
    await db.vouchers.insert_one({
        "_id": code, "type": d["vtype"], "amount": d["amount"],
        "max_claims": 1, "claims": 0, "audience": "all",
        "only_user": target["_id"], "expires_at": expires, "active": True,
        "created_at": utils.now(), "by": user["_id"],
    })
    await audit.log("voucher_create", actor=user["_id"], target=code,
                    vtype=d["vtype"], personal=target["_id"])
    label = _TYPES[d["vtype"]][0]
    await _vc_screen(
        client, user,
        note=(f"🎉 Voucher personal <code>{code}</code> dibuat!\n"
              f"{label} ×{d['amount']} · khusus {utils.esc(target.get('name'))}"),
    )
