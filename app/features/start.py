"""Pintu masuk: /start (deep link), menu utama, bantuan, batal."""
from pyrogram.types import InlineKeyboardMarkup

from app import db
from app.core import router, screens, ui, utils, wallet
from app.core import users as core_users


async def show_main(client, user: dict, note: str = None):
    s = await db.get_settings()
    w = wallet.summary(user, s)
    role = await core_users.get_role(user["_id"])

    lines = [
        ui.title(f"Halo, {utils.esc(user.get('name', 'Anon'))}", "👋"),
        "",
        "Kirim pesan anonim dengan aman — identitasmu tetap rahasia.",
        "",
        ui.field("🔋 Poin hari ini", f"{w['points_left']}/{w['points_quota']}"),
        ui.field("🪙 Coin", w["coins"]),
    ]
    if w["vip"]:
        lines.append(ui.field("👑 VIP", f"aktif s/d {utils.fmt_dt(w['vip_until'])}"))
    if s["paused"]:
        lines += ["", ui.muted("⏸️ Bot sedang jeda sementara — draf tetap aman.")]
    if note:
        lines.insert(0, note + "\n")

    rows = [
        [
            ui.primary("✉️ Kirim Menfes", "m:new"),
            ui.primary("💌 Kirim Confes", "c:new"),
        ],
        [
            ui.btn("👤 Profil", "p:home"),
            ui.btn("📥 Inbox Confes", "ib:home"),
        ],
        [
            ui.btn("🏆 Referral", "p:ref"),
            ui.btn("🎁 Redeem", "p:redeem"),
        ],
        [ui.btn("❓ Bantuan", "menu:help")],
    ]
    if role:
        rows.append([ui.btn("🛠 Panel Admin", "a:home")])
    await _drop_legacy_kb(client, user)

    # Banner dikelola admin lewat panel & disimpan di settings (MongoDB). Tampil
    # hanya bila aktif & ada file_id. Bila kirim gagal, screens.show fallback ke teks.
    content = None
    if s.get("banner_enabled") and s.get("banner_file_id"):
        content = {"type": "photo", "file_id": s["banner_file_id"]}
    await screens.show(
        client, user["_id"], "\n".join(lines), InlineKeyboardMarkup(rows), content=content,
    )


async def _drop_legacy_kb(client, user: dict):
    """Bersihkan menu cepat persisten versi lama (sekali per pengguna).

    Area bawah kini bersih total: navigasi via tombol inline + ≡ Menu Telegram;
    reply keyboard hanya muncul kontekstual saat bot menunggu input.
    """
    if not user.get("nav_kb"):
        return
    await screens.set_reply_kb(client, user["_id"], ui.remove_keyboard())
    await db.users.update_one({"_id": user["_id"]}, {"$unset": {"nav_kb": ""}})
    user.pop("nav_kb", None)


@router.command("start")
async def _cmd_start(client, m, user, payload):
    # pesan /start dari pengguna sengaja TIDAK dihapus (biarkan tetap terlihat)
    await router.clear_state(user["_id"])  # /start selalu memulai bersih
    # layar lama bisa jadi "hantu" bila pengguna baru saja menghapus chat —
    # lupakan, supaya menu dikirim sebagai pesan baru yang pasti terlihat
    await screens.reset(client, user["_id"])
    p = (payload or "").strip()
    note = None
    if p.startswith("ref_"):
        from app.features import profile
        note = await profile.apply_referral(client, user, p[4:])
    elif p.startswith("w_"):
        from app.features import confes
        return await confes.accept_token(client, user, p[2:])
    elif p.startswith("x_"):
        from app.features import confes
        return await confes.decline_token(client, user, p[2:])
    elif p == "menfes":
        from app.features import menfes
        return await menfes.begin_flow(client, user)
    elif p == "confes":
        from app.features import confes
        return await confes.begin_flow(client, user)
    await show_main(client, user, note=note)


@router.command("menu")
async def _cmd_menu(client, m, user, payload):
    await screens.drop(client, m)
    await router.clear_state(user["_id"])
    await show_main(client, user)


@router.command("batal")
async def _cmd_batal(client, m, user, payload):
    await screens.drop(client, m)
    await db.drafts.update_many(
        {"user_id": user["_id"], "status": "draft"}, {"$set": {"status": "cancelled"}}
    )
    await router.clear_state(user["_id"])
    await show_main(client, user, note="🗑 Semua draf dibatalkan.")


@router.command("help")
async def _cmd_help(client, m, user, payload):
    await screens.drop(client, m)
    await _help(client, user)


@router.cb("menu", "home")
async def _cb_home(client, cq, args, user):
    await router.clear_state(user["_id"])
    await show_main(client, user)


# ---- tombol 🗑 Batal universal pada keyboard input kontekstual ----

@router.nav(ui.CANCEL)
async def _nav_cancel(client, m, user):
    """Satu tombol batal untuk semua prompt input — sadar konteks:
    draf berisi → kembali ke preview; alur admin → kembali ke panel;
    alur profil → kembali ke profil; selain itu → menu utama."""
    st = await router.get_state(user["_id"])
    name = (st or {}).get("name", "")
    await router.clear_state(user["_id"])

    if name.startswith("draft."):
        from app.core import drafts
        draft = await drafts.get_active((st["data"] or {}).get("draft_id"), user["_id"])
        if draft and draft.get("content"):
            return await drafts.show_preview(client, user, draft)
        if draft:
            await drafts.set_status(draft["_id"], "cancelled")
        return await show_main(client, user, note="🗑 Draf dibatalkan. Santai, tidak ada yang terkirim.")

    if name.startswith("admin."):
        from app.features.admin import panel
        return await panel.show_home(client, user, note="🗑 Dibatalkan.")

    if name.startswith("profile."):
        from app.features import profile
        return await profile.show(client, user, note="🗑 Dibatalkan.")

    await show_main(client, user)


@router.cb("menu", "help")
async def _cb_help(client, cq, args, user):
    await _help(client, user)


async def _help(client, user):
    text = (
        ui.title("Panduan", "❓") + "\n"
        + ui.muted("Ketuk tiap bagian untuk membuka.") + "\n\n"
        + ui.quote(
            "✉️ <b>Menfes</b>\n"
            "Pesan anonim yang tampil di channel. Biaya 1 poin harian "
            "(reset 00:00 WIB), atau 1 coin bila poin habis. VIP tanpa batas. "
            "Bisa ditambah mood & hashtag.",
            expandable=True,
        )
        + "\n" + ui.quote(
            "💌 <b>Confes</b>\n"
            "Pesan anonim langsung ke orangnya — gratis. Target diberi tahu "
            "satu kali. Jika ia menerima, confes-mu tampil (ketuk untuk membuka). "
            "Target bisa jeda, tolak media, atau blokir kapan saja.",
            expandable=True,
        )
        + "\n" + ui.quote(
            "↩️ <b>Balas Anonim</b>\n"
            "Penerima bisa membalas tanpa tahu siapa kamu — dua arah dengan alias "
            "Anon A & Anon B. Percakapan berakhir otomatis dan bisa dihentikan kapan saja.",
            expandable=True,
        )
        + "\n" + ui.quote(
            "🪙 <b>Coin & VIP</b>\n"
            "Coin dipakai saat poin harian habis. Dapat coin lewat referral atau "
            "voucher dari admin. VIP membebaskan batas harian. Coin tidak hangus.",
            expandable=True,
        )
    )
    kb = InlineKeyboardMarkup([[ui.btn("🏠 Menu Utama", "menu:home")]])
    await screens.show(client, user["_id"], text, kb)
