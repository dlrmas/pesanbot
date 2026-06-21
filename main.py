"""Titik masuk: jalankan bot utama + akun asisten (opsional)."""
import asyncio
import logging
import os

from pyrogram import idle
from pyrogram.types import BotCommand, MenuButtonCommands

from app import clients, config, db
from app.assistant import runner
from app.core import router
from app.features import confes, inline, menfes, profile, replies, start  # noqa: F401 (daftar handler)
from app.features import admin  # noqa: F401 (daftar handler admin)

log = logging.getLogger("main")


def _setup_logging():
    """Terminal tenang: aplikasi bicara seperlunya, pustaka pihak ketiga
    hanya saat ada peringatan/error. Override lewat env LOG_LEVEL bila perlu."""
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    for noisy in ("pyrogram", "pymongo"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))


async def main():
    _setup_logging()
    cfg = config.load()
    await db.init(cfg)
    log.info("MongoDB siap (db: %s)", cfg.db_name)  # URI tidak dicetak: berisi kredensial

    bot = clients.build_bot(cfg)
    router.attach(bot)
    inline.attach(bot)

    await bot.start()
    me = await bot.get_me()
    clients.bot_username = me.username
    log.info("Bot utama aktif: @%s", me.username)

    try:  # daftar command + menu button (CLAUDE.md §11) — best effort
        await bot.set_bot_commands([
            BotCommand("start", "Mulai / buka menu utama"),
            BotCommand("menu", "Menu utama"),
            BotCommand("profil", "Profil, saldo & lencana"),
            BotCommand("batal", "Batalkan draf / alur berjalan"),
            BotCommand("help", "Panduan singkat"),
        ])
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception as e:
        log.warning("Gagal mengatur command/menu button: %s", e)

    if cfg.assistant_session:
        try:
            await runner.start(cfg)
        except Exception as e:
            log.warning("Asisten gagal start (%s) — fitur peringatan confes menunggu.", e)
    else:
        log.info("ASSISTANT_SESSION kosong — peringatan confes menunggu asisten.")

    from app.features.admin import updater
    await updater.startup_notice(bot)  # lapor ke admin bila ini hasil restart update

    from app.features.admin import broadcast
    await broadcast.resume_pending(bot)  # lanjutkan broadcast yang terputus restart

    log.info("🤫 Menfes & Confes siap melayani.")
    await idle()

    await runner.stop()
    await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
