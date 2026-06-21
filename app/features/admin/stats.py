"""Admin: statistik — catatan bot lengkap dalam satu layar ringkas."""
from datetime import timezone

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app import db
from app.core import hashtags, router, screens, ui, utils
from app.core import audit as audit_mod


def _today_start_utc():
    w = utils.now_wib().replace(hour=0, minute=0, second=0, microsecond=0)
    return w.astimezone(timezone.utc)


@router.cb("a", "st", admin=True)
async def _cb_stats(client, cq, args, user):
    t0 = _today_start_utc()

    n_users = await db.users.count_documents({})
    n_new = await db.users.count_documents({"joined_at": {"$gte": t0}})
    n_banned = await db.users.count_documents({"banned": True})
    n_vip = await db.users.count_documents({"vip_until": {"$gt": utils.now()}})

    n_post = await db.posts.count_documents({})
    n_post_t = await db.posts.count_documents({"created_at": {"$gte": t0}})
    n_conf = await db.confessions.count_documents({})
    n_conf_del = await db.confessions.count_documents({"status": "delivered"})
    n_conf_pen = await db.confessions.count_documents({"status": "pending"})
    n_thread = await db.threads.count_documents({"status": "active"})

    n_rev = await db.drafts.count_documents({"status": "review"})
    n_rep = await db.reports.count_documents({"status": "open"})
    n_vc = await db.vouchers.count_documents({"active": True})
    n_claims = await db.voucher_claims.count_documents({})
    n_ref = await db.ledger.count_documents({"note": "bonus mengajak teman"})

    coins_out = 0
    # PyMongo Async: aggregate() adalah coroutine — wajib di-await dulu
    cursor = await db.ledger.aggregate([
        {"$match": {"kind": "coin", "delta": {"$gt": 0}}},
        {"$group": {"_id": None, "n": {"$sum": "$delta"}}},
    ])
    async for row in cursor:
        coins_out = row["n"]

    top_tags = (await hashtags.all_tags())[:5]
    tag_line = " ".join(f"#{t['tag']}({t.get('uses', 0)})" for t in top_tags) or "-"

    risky = []
    async for u in db.users.find({"risk.reports": {"$gt": 0}}).sort("risk.reports", -1).limit(3):
        score, _ = audit_mod.risk(u)
        risky.append(f"   {audit_mod.risk_badge(score)} <code>{u['_id']}</code> {utils.esc(u.get('name', ''))}")

    s = await db.get_settings()
    sysflags = (
        f"menfes {'🟢' if s['menfes_enabled'] else '🔴'} · "
        f"confes {'🟢' if s['confes_enabled'] else '🔴'} · "
        f"asisten {'🟢' if s['assistant_enabled'] else '🔴'} · "
        f"{'⏸️ jeda' if s['paused'] else '▶️ berjalan'}"
    )
    lines = [
        ui.title("Statistik Bot", "📊"),
        ui.muted(utils.fmt_dt(utils.now())),
        "",
        "<b>👥 Pengguna</b>",
        f"{n_users} total · +{n_new} hari ini · {n_banned} banned · {n_vip} VIP",
        "",
        "<b>📨 Konten</b>",
        f"Menfes {n_post} ({n_post_t} hari ini)",
        f"Confes {n_conf} · {n_conf_del} tampil · {n_conf_pen} pending",
        f"Thread aktif {n_thread}",
        "",
        "<b>🛡 Moderasi & Ekonomi</b>",
        f"Review {n_rev} · Laporan {n_rep}",
        f"Voucher {n_vc} aktif · {n_claims} klaim",
        f"Referral {n_ref} · {coins_out} coin beredar",
        "",
        "<b>#️⃣ Top hashtag</b>",
        utils.esc(tag_line),
    ]
    if risky:
        lines += ["", "<b>🎯 Risiko tertinggi</b>"] + risky
    lines += ["", ui.muted(sysflags)]
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔎 Cari User", callback_data="a:us"),
            InlineKeyboardButton("🚫 Daftar Ban", callback_data="a:us:banned"),
        ],
        [InlineKeyboardButton("🔄 Segarkan", callback_data="a:st")],
        [InlineKeyboardButton("🛠 Panel", callback_data="a:home")],
    ])
    await screens.show(client, user["_id"], "\n".join(lines), kb)
