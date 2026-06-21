"""Alur menfes — tipis: semua kerja berat ada di mesin drafts/delivery."""
from pyrogram.types import InlineKeyboardButton

from app import db
from app.core import drafts, ratelimit, router, screens, utils, wallet
from app.core import users as core_users


@router.cb("m", "new")
async def _cb_new(client, cq, args, user):
    await begin_flow(client, user)


async def begin_flow(client, user: dict):
    s = await db.get_settings()
    if not s["menfes_enabled"] or s["paused"]:
        return await screens.show(
            client, user["_id"],
            "😴 Fitur menfes sedang nonaktif. Coba lagi nanti ya.",
            drafts.home_kb(),
        )
    left = ratelimit.cooldown_left(user, "menfes", s)
    if left:
        return await screens.show(
            client, user["_id"],
            f"⏳ Pelan-pelan! Menfes berikutnya bisa dikirim dalam <b>{utils.left_str(left)}</b>.",
            drafts.home_kb(),
        )
    fresh = await core_users.get(user["_id"]) or user
    method, label = wallet.quote(fresh, s)
    if not method:
        kb = drafts.home_kb([[
            InlineKeyboardButton("🏆 Referral", callback_data="p:ref"),
            InlineKeyboardButton("🎁 Redeem", callback_data="p:redeem"),
        ]])
        return await screens.show(
            client, user["_id"],
            "😬 <b>Poin harian habis & coin kosong</b>\n"
            "Poin akan terisi lagi pukul 00:00 WIB.\n"
            "Atau kumpulkan coin: ajak teman (referral) / redeem voucher 🪙",
            kb,
        )
    draft = await drafts.begin(user["_id"], "menfes")
    await drafts.prompt_content(
        client, user, draft, note=f"💸 Biaya kirim: <b>{label}</b>"
    )
