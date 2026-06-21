"""Inline mode bot utama.

Dua kegunaan:
1. Asisten meminta hasil inline `w_<token>` → kartu peringatan confes bertombol
   (token tidak pernah berisi isi confes — hanya kunci lookup).
2. Pengguna lain mengetik @bot → kartu promosi ajakan kirim menfes/confes.
"""
import logging

from pyrogram.handlers import InlineQueryHandler
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InlineQueryResultCachedPhoto,
    InputTextMessageContent,
)

from app import clients, db
from app.core import utils
from app.core.normalize import normalize

log = logging.getLogger("inline")


async def _search_results(q: str) -> list:
    """Cari menfes berdasarkan hashtag yang cocok dengan kata kunci."""
    terms = [t for t in normalize(q).split() if len(t) >= 2]
    if not terms:
        return []
    results, seen = [], set()
    cursor = db.posts.find(
        {"tags": {"$in": terms}, "link": {"$ne": None}}
    ).sort("created_at", -1).limit(12)
    async for p in cursor:
        link = p.get("link")
        if not link or link in seen:
            continue
        seen.add(link)
        tagstr = " ".join("#" + t for t in (p.get("tags") or [])) or "menfes"
        body = (
            f"📨 <b>Menfes</b> {utils.esc(tagstr)}\n"
            f"🔗 {link}\n\n"
            f"🤫 Kirim menfes-mu juga di @{clients.bot_username}"
        )
        results.append(InlineQueryResultArticle(
            title=f"📨 {tagstr}",
            input_message_content=InputTextMessageContent(
                body, parse_mode=utils.HTML, link_preview_options=utils.NOPREV,
            ),
            id=f"post-{p['_id']}",
            description="Bagikan tautan menfes ini",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Lihat di Channel", url=link)],
            ]),
        ))
    return results


async def _on_inline(client, iq):
    q = (iq.query or "").strip()
    if q.startswith("w_") and clients.assistant_id and iq.from_user.id == clients.assistant_id:
        token = q[2:]
        tdoc = await db.targets.find_one({"token": token})
        if tdoc:
            accept = f"https://t.me/{clients.bot_username}?start=w_{token}"
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("💌 Lihat & Terima", url=accept)],
                [InlineKeyboardButton("🙈 Tolak", callback_data=f"c:dec:{token}")],
            ])
            body = (
                "💌 <b>Ada pesan anonim untukmu</b>\n\n"
                f"Seseorang mengirim confes lewat @{clients.bot_username}. "
                "Identitas pengirim dirahasiakan, dan kamu bebas memilih: "
                "terima untuk membacanya, atau tolak agar tidak dihubungi lagi.\n\n"
                "<i>Pemberitahuan ini dikirim satu kali saja.</i>"
            )
            # banner panel ikut tampil di kartu asisten bila aktif (lebih tepercaya);
            # file_id banner valid karena diunggah ke bot utama ini.
            s = await db.get_settings()
            banner = s.get("banner_file_id") if s.get("banner_enabled") else None
            if banner:
                result = InlineQueryResultCachedPhoto(
                    photo_file_id=banner,
                    id=f"warn-{token}",
                    caption=body,
                    parse_mode=utils.HTML,
                    reply_markup=kb,
                )
            else:
                result = InlineQueryResultArticle(
                    title="💌 Pesan Anonim",
                    input_message_content=InputTextMessageContent(
                        body, parse_mode=utils.HTML, link_preview_options=utils.NOPREV
                    ),
                    id=f"warn-{token}",
                    description="Kartu peringatan confes",
                    reply_markup=kb,
                )
            try:
                return await iq.answer([result], cache_time=1, is_personal=True)
            except Exception as e:
                log.info("answer inline warn gagal: %s", e)
                return

    # pencarian menfes berdasarkan hashtag (ketik @bot <kata>)
    if q and not q.startswith("w_"):
        results = await _search_results(q)
        if results:
            try:
                return await iq.answer(results, cache_time=10, is_personal=False)
            except Exception as e:
                log.info("answer inline search gagal: %s", e)
                return

    # kartu promosi untuk semua orang (dipakai juga tombol 📤 Bagikan referral)
    start = f"https://t.me/{clients.bot_username}?start=menfes"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Mulai", url=start)]])
    body = (
        f"✉️ <b>Kirim pesan anonim lewat @{clients.bot_username}</b>\n\n"
        "<blockquote>Menfes — tampil anonim di channel\n"
        "Confes — pesan langsung ke orangnya\n"
        "Balas anonim — ngobrol dua arah tanpa identitas</blockquote>\n"
        "Identitasmu tetap rahasia."
    )
    results = [InlineQueryResultArticle(
        title="✉️ Kirim pesan anonim",
        input_message_content=InputTextMessageContent(
            body, parse_mode=utils.HTML, link_preview_options=utils.NOPREV
        ),
        id="promo",
        description="Ajak teman kirim menfes / confes",
        reply_markup=kb,
    )]
    try:
        # tombol di atas hasil inline → bawa pengguna ke chat privat bot (§11)
        await iq.answer(
            results, cache_time=300, is_personal=False,
            switch_pm_text="Buka bot & mulai kirim",
            switch_pm_parameter="menfes",
        )
    except Exception as e:
        log.info("answer inline promo gagal: %s", e)


def attach(bot):
    bot.add_handler(InlineQueryHandler(_on_inline))
