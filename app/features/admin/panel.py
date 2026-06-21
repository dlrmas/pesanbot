"""Beranda panel admin + notifikasi ke admin (ping review/laporan)."""
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app import config, db
from app.core import ratelimit, router, screens, utils
from app.core import users as core_users

# Sumber tunggal baris tombol "kembali ke beranda panel" — dipakai semua submodul
# admin (hindari duplikasi konstanta, CLAUDE.md §3).
BACK = [InlineKeyboardButton("🛠 Panel", callback_data="a:home")]


async def admin_ids() -> list[int]:
    ids = set(config.cfg.owner_ids)
    async for a in db.admins.find({}):
        ids.add(a["_id"])
    return list(ids)


@router.command("admin", admin=True)
async def _cmd_admin(client, m, user, payload):
    await screens.drop(client, m)
    await show_home(client, user)


@router.cb("a", "home", admin=True)
async def _cb_home(client, cq, args, user):
    await show_home(client, user)


async def show_home(client, user: dict, note: str = None):
    role = await core_users.get_role(user["_id"])
    n_rev = await db.drafts.count_documents({"status": "review"})
    n_rep = await db.reports.count_documents({"status": "open"})
    s = await db.get_settings()

    flags = []
    if s["paused"]:
        flags.append("⏸️ jeda")
    if not s["menfes_enabled"]:
        flags.append("✉️ menfes off")
    if not s["confes_enabled"]:
        flags.append("💌 confes off")
    if not s["assistant_enabled"]:
        flags.append("🤖 asisten off")
    status = " · ".join(flags) if flags else "✅ semua sistem normal"

    text = (
        f"🛠 <b>PANEL ADMIN</b> · {'👑 Owner' if role == 'owner' else '🛡 Moderator'}\n"
        f"{status}\n"
        f"🕵️ Review menunggu: <b>{n_rev}</b> · 🚩 Laporan terbuka: <b>{n_rep}</b>"
    )
    if note:
        text = f"{note}\n\n{text}"

    rows = [
        [
            InlineKeyboardButton("📊 Stats & User", callback_data="a:st"),
            InlineKeyboardButton(f"🕵️ Review ({n_rev + n_rep})", callback_data="a:rv"),
        ],
    ]
    if role == "owner":
        rows += [
            [
                InlineKeyboardButton("📣 Broadcast", callback_data="a:bc"),
                InlineKeyboardButton("🎟 Voucher", callback_data="a:vc"),
            ],
            [
                InlineKeyboardButton("👑 VIP", callback_data="a:vip"),
                InlineKeyboardButton("📢 Channel", callback_data="a:ch"),
            ],
            [InlineKeyboardButton("🖼 Banner Menu", callback_data="a:bn")],
            [
                InlineKeyboardButton("#️⃣ Hashtag", callback_data="a:ht"),
                InlineKeyboardButton("🎭 Mood", callback_data="a:md"),
            ],
            [
                InlineKeyboardButton("🧹 Kata Terlarang", callback_data="a:wd"),
                InlineKeyboardButton("🧑‍⚖️ Moderator", callback_data="a:rl"),
            ],
            [
                InlineKeyboardButton("⚙️ Konfigurasi", callback_data="a:cf"),
                InlineKeyboardButton("🔄 Update Bot", callback_data="a:up"),
            ],
            [InlineKeyboardButton("🔁 Restart Bot", callback_data="a:rs")],
        ]
    rows.append([InlineKeyboardButton("🏠 Menu Utama", callback_data="menu:home")])
    await screens.show(client, user["_id"], text, InlineKeyboardMarkup(rows))


async def _ping(client, key: str, text: str):
    """Beri tahu para admin — maks sekali per 5 menit per jenis (anti-banjir)."""
    if not await ratelimit.lock(f"ping:{key}", 300):
        return
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🕵️ Buka Review", callback_data="a:rv")]])
    for aid in await admin_ids():
        await screens.notify(client, aid, text, kb=kb)


async def ping_review(client):
    n = await db.drafts.count_documents({"status": "review"})
    await _ping(client, "review", f"🕵️ <b>{n} konten</b> menunggu review admin.")


async def ping_reports(client):
    n = await db.reports.count_documents({"status": "open"})
    await _ping(client, "reports", f"🚩 <b>{n} laporan</b> menunggu ditindak.")


async def ping_error(client, where: str):
    """Beri tahu admin saat ada error saat memproses — throttle 5 menit (anti-banjir)."""
    if not await ratelimit.lock("ping:error", 300):
        return
    for aid in await admin_ids():
        await screens.notify(
            client, aid,
            f"⚠️ <b>Terjadi error</b> di <code>{utils.esc(where)}</code>.\n"
            "Cek log server untuk detailnya.",
        )
