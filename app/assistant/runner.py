"""Akun asisten (Kurigram, session string).

Tugas: mengirim SATU KALI peringatan confes ke target — diutamakan lewat hasil
inline bot utama agar pesan membawa tombol; fallback teks polos berisi deep link.
Berhenti otomatis (assistant_enabled=False) saat kena limit Telegram.
"""
import asyncio
import logging

from pyrogram.errors import FloodWait, RPCError

from app import clients, config, db
from app.core import audit, screens

log = logging.getLogger("assistant")

_WARN_PLAIN = (
    "Halo 👋 Ini @{bot} — layanan pesan anonim (menfes & confes).\n\n"
    "Ada satu pesan anonim yang ditujukan untukmu. Aku akun pengantar resmi "
    "bot ini; isi pesan dan identitas pengirim tidak aku ketahui.\n\n"
    "Baca pesannya: {accept}\n"
    "Tidak mau menerima: {decline} (kamu tidak akan dihubungi lagi)\n\n"
    "Dikirim sekali saja. Kalau ragu, abaikan saja pesan ini."
)


async def start(cfg) -> bool:
    client = clients.build_assistant(cfg)
    if not client:
        return False
    try:
        await client.start()
    except Exception:
        # gagal start (mis. AUTH_KEY_DUPLICATED): jangan biarkan objek klien
        # setengah-hidup tertinggal — kalau tidak, send_warning akan mencoba
        # memakainya dan menabrak "Client has not been started yet".
        clients.assistant = None
        raise
    me = await client.get_me()
    clients.assistant_id = me.id
    log.info("asisten aktif sebagai %s (%s)", me.first_name, me.id)
    asyncio.create_task(_retry_warnings_soon())  # susul confes yang sempat tertunda
    return True


async def _retry_warnings_soon():
    """Setelah asisten siap, susulkan peringatan confes yang tertunda (best effort)."""
    await asyncio.sleep(5)
    try:
        from app.core import delivery
        n = await delivery.retry_pending_warnings(clients.bot)
        if n:
            log.info("menyusulkan %s peringatan confes tertunda", n)
    except Exception:
        log.exception("retry_pending_warnings gagal")


async def stop():
    if clients.assistant:
        try:
            await clients.assistant.stop()
        except Exception:
            pass


async def send_warning(target: dict, token: str) -> tuple[bool, str]:
    """Kirim peringatan pertama ke target. -> (ok, alasan_gagal).

    alasan: '' | 'disabled' | 'limit' | 'unreachable'
    """
    s = await db.get_settings()
    if not s.get("assistant_enabled") or not clients.assistant:
        return False, "disabled"

    peer = target.get("username") or target.get("id")
    accept = f"https://t.me/{clients.bot_username}?start=w_{token}"
    decline = f"https://t.me/{clients.bot_username}?start=x_{token}"

    try:
        sent = False
        try:
            # jalur utama: hasil inline bot utama (membawa tombol bot)
            r = await clients.assistant.get_inline_bot_results(
                clients.bot_username, f"w_{token}"
            )
            if r.results:
                await clients.assistant.send_inline_bot_result(
                    peer, r.query_id, r.results[0].id
                )
                sent = True
        except FloodWait:
            raise
        except Exception as e:
            log.info("inline gagal (%s), pakai fallback teks", e)
        if not sent:
            await clients.assistant.send_message(
                peer, _WARN_PLAIN.format(bot=clients.bot_username, accept=accept, decline=decline)
            )
        await audit.log("confes_warning", target=target.get("id"), via="assistant")
        return True, ""
    except FloodWait as e:
        # patuh limit Telegram: matikan asisten, beri tahu owner
        await db.update_settings(assistant_enabled=False)
        await audit.log("assistant_disabled", target=target.get("id"), floodwait=e.value)
        for oid_ in config.cfg.owner_ids:
            await screens.notify(
                clients.bot, oid_,
                "⚠️ <b>Asisten dimatikan otomatis</b>\n"
                f"Kena FloodWait {e.value} dtk saat mengirim peringatan confes. "
                "Aktifkan lagi dari ⚙️ Konfigurasi bila sudah aman.",
            )
        return False, "limit"
    except RPCError as e:
        log.info("target tak terjangkau: %s", e)
        return False, "unreachable"
    except Exception:
        log.exception("send_warning gagal")
        return False, "unreachable"
