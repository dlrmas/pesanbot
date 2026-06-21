"""Klien Telegram: bot utama (BotFather) dan akun asisten (session string)."""
import os
from pathlib import Path

from pyrogram import Client

# Path session ABSOLUT (bukan relatif ke cwd) supaya tetap sama setelah restart
# yang menjalankan ulang proses dengan cwd berbeda — file session & auth key
# tidak hilang/terduplikasi.
SESSIONS = Path(__file__).resolve().parent.parent / "sessions"

bot: Client = None
assistant: Client = None
bot_username: str = ""
assistant_id: int = 0


def build_bot(cfg) -> Client:
    global bot
    os.makedirs(SESSIONS, exist_ok=True)
    bot = Client(
        "menfes_bot",
        api_id=cfg.api_id,
        api_hash=cfg.api_hash,
        bot_token=cfg.bot_token,
        workdir=str(SESSIONS),
    )
    return bot


def build_assistant(cfg) -> Client | None:
    global assistant
    if not cfg.assistant_session:
        return None
    assistant = Client(
        "assistant",
        api_id=cfg.api_id,
        api_hash=cfg.api_hash,
        session_string=cfg.assistant_session,
        in_memory=True,
    )
    return assistant
