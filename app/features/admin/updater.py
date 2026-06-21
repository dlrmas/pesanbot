"""Admin (Owner): update bot dari GitHub langsung lewat panel.

Alur: cek git clone → fetch origin (repo public / kredensial tersedia) →
bila ditolak, minta token GitHub — token dipakai SEKALI di memori untuk
fetch, TIDAK disimpan di mana pun, tidak di-log, dan pesan berisi token
langsung dihapus. Merge selalu fast-forward; restart dengan konfirmasi.
"""
import asyncio
import base64
import logging
import os
import re
import sys
from pathlib import Path

from pyrogram.types import InlineKeyboardMarkup

from app import clients, db
from app.assistant import runner
from app.core import audit, ratelimit, router, screens, ui, utils
from app.features.admin.panel import BACK as _BACK

log = logging.getLogger("updater")

ROOT = Path(__file__).resolve().parents[3]
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-\.]{8,255}$")


# ---------------------------------------------------------------- subprocess

# env yang diwariskan ke git/pip yang harus dibersihkan agar:
# - git gagal cepat alih-alih memanggil askpass helper (mis. VS Code) → menggantung;
# - trace verbose tidak membocorkan header Authorization ke stderr.
_STRIP_ENV = (
    "GIT_ASKPASS", "SSH_ASKPASS", "GIT_CONFIG_PARAMETERS",
    "GIT_TRACE", "GIT_TRACE_CURL", "GIT_CURL_VERBOSE", "GIT_TRACE_PACKET",
    "GIT_CONFIG_COUNT",  # buang sisa milik lingkungan; punya kita diset ulang via env_extra
)


def _base_env(env_extra: dict = None) -> dict:
    env = dict(os.environ)
    for k in _STRIP_ENV:
        env.pop(k, None)
    env.update({
        "GIT_TERMINAL_PROMPT": "0",  # dilarang prompt terminal
        "GIT_TRACE_REDACT": "1",     # paksa redaksi header rahasia di trace git
        "GIT_ASKPASS": "",           # tak ada helper → kredensial kosong → gagal cepat
        "GCM_INTERACTIVE": "never",  # Git Credential Manager (Windows) dilarang interaktif
    })
    if env_extra:
        env.update(env_extra)
    return env


async def _run(*args, timeout: int = 90, env_extra: dict = None):
    """Jalankan perintah tanpa shell (anti-injeksi). -> (rc, out, err)."""
    try:
        p = await asyncio.create_subprocess_exec(
            *args, cwd=str(ROOT),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=_base_env(env_extra),
        )
    except (FileNotFoundError, NotImplementedError) as e:
        return 127, "", f"{args[0]}: {type(e).__name__}"
    try:
        out, err = await asyncio.wait_for(p.communicate(), timeout)
    except asyncio.TimeoutError:
        try:
            p.kill()
            await p.wait()  # reap: cegah transport yatim & lock .git tertinggal
        except Exception:
            pass
        return 124, "", "waktu habis"
    return p.returncode, out.decode(errors="replace").strip(), err.decode(errors="replace").strip()


async def _git(*args, timeout: int = 90, env_extra: dict = None):
    # credential.helper & core.askpass dikosongkan: token tidak boleh tersangkut
    # di cache git, dan git tak boleh memanggil helper apa pun untuk bertanya.
    return await _run("git", "-c", "credential.helper=", "-c", "core.askpass=", *args,
                      timeout=timeout, env_extra=env_extra)


def _redact(text: str, secret: str = None) -> str:
    """Pastikan token/kredensial tidak pernah tampil di layar atau log."""
    text = text or ""
    if secret:
        text = text.replace(secret, "•••")
    text = re.sub(r"://[^/\s]*@", "://•••@", text)  # kredensial di URL
    # header auth (jaga-jaga bila trace verbose aktif di lingkungan operator)
    text = re.sub(r"(?i)authorization:\s*\S+\s+\S+", "Authorization: •••", text)
    return text


def _clean_url(remote_url: str) -> str | None:
    """URL https BERSIH (tanpa kredensial) dari remote yang ada (https/ssh)."""
    m = re.match(r"^git@([^:/]+):(.+?)(?:\.git)?/?$", remote_url or "")
    if not m:
        m = re.match(r"^https?://(?:[^@/]+@)?([^/]+)/(.+?)(?:\.git)?/?$", remote_url or "")
    if not m:
        return None
    return f"https://{m.group(1)}/{m.group(2)}.git"


def _auth_env(token: str) -> dict:
    """Auth lewat environment, BUKAN argv/URL — token tidak terlihat di daftar
    proses dan tidak mungkin terbawa ke pesan error git."""
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.extraHeader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
    }


async def _version() -> tuple[str, str]:
    rc, out, _ = await _git("log", "-1", "--format=%h|%s", timeout=20)
    if rc != 0 or "|" not in out:
        return "?", ""
    h, _, s = out.partition("|")
    return h, s


async def _git_version() -> tuple[int, int]:
    rc, out, _ = await _run("git", "--version", timeout=20)
    m = re.search(r"(\d+)\.(\d+)", out or "")
    return (int(m.group(1)), int(m.group(2))) if rc == 0 and m else (0, 0)


# ---------------------------------------------------------------- layar

async def _screen(client, uid: int, text: str, rows: list = None):
    kb_rows = list(rows or [])
    kb_rows.append(_BACK)
    await screens.show(client, uid, text, InlineKeyboardMarkup(kb_rows))


@router.cb("a", "up", owner=True)
async def _cb_update(client, cq, args, user):
    sub = args[0] if args else "home"

    if sub == "home":
        await _safe_answer(cq, "⏳ Memeriksa pembaruan…")
        return await _check(client, user)

    if sub == "tok":
        await router.set_state(user["_id"], "admin.up_token")
        return await screens.ask(
            client, user["_id"],
            "🔑 <b>Token GitHub Dibutuhkan</b>\n"
            "Repo ini private, jadi aku perlu token untuk mengambil update.\n\n"
            + ui.quote(
                "🔐 Token <b>tidak disimpan di mana pun</b> — dipakai sekali "
                "untuk mengambil update, lalu dilupakan.\n"
                "🧹 Pesan berisi token akan langsung kuhapus.\n"
                "💡 Cukup beri akses <i>read-only (Contents)</i> pada repo ini."
            ),
            placeholder="ghp_… / github_pat_…",
        )

    if sub == "go":
        # Tanpa konfirmasi kedua: layar cek sudah menampilkan changelog + tombol
        # "Update Sekarang" sebagai persetujuan. TTL lock > durasi _apply
        # (merge 60 + pip 300 + margin) agar tak kedaluwarsa di tengah jalan.
        if not await ratelimit.lock("bot:update", 600):
            return await _safe_answer(cq, "⏳ Update sedang berjalan…")
        try:
            return await _apply(client, user)
        finally:
            await ratelimit.unlock("bot:update")


async def _safe_answer(cq, text: str = None):
    try:
        await cq.answer(text)
    except Exception:
        pass


# ---------------------------------------------------------------- cek update

async def _check(client, user: dict, token: str = None):
    uid = user["_id"]
    await screens.show(client, uid, "⏳ <i>Memeriksa pembaruan…</i>")

    rc, _, err = await _git("rev-parse", "--is-inside-work-tree", timeout=20)
    if rc != 0:
        return await _screen(
            client, uid,
            "🚫 <b>Bukan Git Clone</b>\n"
            + ui.quote(f"Folder bot ini bukan hasil <code>git clone</code>, "
                       f"jadi update otomatis tidak tersedia.\n{utils.esc(_redact(err))}"),
        )

    cur, cur_msg = await _version()
    rc, branch, _ = await _git("rev-parse", "--abbrev-ref", "HEAD", timeout=20)
    branch = branch if rc == 0 and branch != "HEAD" else ""

    # fetch: tanpa token → remote origin apa adanya (public/SSH/kredensial ada);
    # dengan token → URL bersih + auth via environment (token tak menyentuh
    # argv, URL, konfigurasi git, maupun pesan error)
    auth = None
    if token:
        rc_u, remote, _ = await _git("remote", "get-url", "origin", timeout=20)
        url = _clean_url(remote) if rc_u == 0 else None
        if not url:
            return await _screen(
                client, uid,
                "🚫 Remote <code>origin</code> tidak dikenali — update manual dulu ya.",
            )
        auth = _auth_env(token)
        fetch_args = ["fetch", url] + ([branch] if branch else [])
    else:
        fetch_args = ["fetch", "origin"] + ([branch] if branch else [])
    rc, _, err = await _git(*fetch_args, timeout=120, env_extra=auth)

    if rc != 0:
        err_view = utils.esc(utils.take(_redact(err, token), 300))
        if token:
            return await _screen(
                client, uid,
                "❌ <b>Token Ditolak</b>\n"
                + ui.quote(f"{err_view}\n\nPastikan token punya akses baca ke repo "
                           "ini dan git di server versi ≥ 2.31.")
                + "\n🔐 Token tadi sudah dilupakan.",
                [[ui.btn("🔑 Coba Token Lain", "a:up:tok")]],
            )
        return await _screen(
            client, uid,
            "🔒 <b>Repo Private Terdeteksi</b>\n"
            "Fetch tanpa autentikasi ditolak — aku butuh token GitHub.\n"
            + ui.quote("🔐 Token hanya dipakai sekali di memori, "
                       "tidak disimpan, tidak dicatat di log."),
            [[ui.primary("🔑 Masukkan Token", "a:up:tok")]],
        )

    rc, n_raw, _ = await _git("rev-list", "--count", "HEAD..FETCH_HEAD", timeout=20)
    n = utils.parse_int(n_raw) if rc == 0 else None
    await audit.log("bot_update_check", actor=uid, behind=n, via="token" if token else "origin")

    if not n:
        return await _screen(
            client, uid,
            "✅ <b>Bot Sudah Versi Terbaru</b>\n"
            + ui.quote(f"📌 <code>{utils.esc(cur)}</code> · {utils.esc(utils.take(cur_msg, 60))}"),
            [[ui.btn("🔄 Periksa Lagi", "a:up")]],
        )

    _, log_out, _ = await _git("log", "--oneline", "--no-decorate", "-8",
                               "HEAD..FETCH_HEAD", timeout=20)
    _, files, _ = await _git("diff", "--name-only", "HEAD..FETCH_HEAD", timeout=20)
    req_note = (
        "\n📦 <i>requirements.txt ikut berubah — dependensi akan dipasang otomatis.</i>"
        if "requirements.txt" in (files or "") else ""
    )
    _, dirty, _ = await _git("status", "--porcelain", timeout=20)
    dirty_note = (
        "\n⚠️ <i>Ada perubahan lokal pada file — update bisa gagal fast-forward.</i>"
        if (dirty or "").strip() else ""
    )
    await _screen(
        client, uid,
        f"🆕 <b>{n} Pembaruan Tersedia</b>\n"
        f"Versi sekarang: <code>{utils.esc(cur)}</code>\n"
        + ui.quote(utils.esc(utils.take(log_out, 700)))
        + req_note + dirty_note,
        [[ui.success(f"⬇️ Update Sekarang ({n} commit)", "a:up:go")]],
    )


@router.state("admin.up_token")
async def _st_token(client, m, user, st):
    token = (m.text or "").strip()
    dropped = await screens.drop(client, m)  # hapus segera — pesan berisi rahasia
    if not dropped:
        await screens.notify(
            client, user["_id"],
            "⚠️ <b>Aku gagal menghapus pesan berisi token-mu.</b>\n"
            "Hapus manual dari chat ini, dan sebaiknya <b>cabut token itu</b> di "
            "GitHub lalu buat yang baru — demi keamanan.",
        )
    if await _owner_only(user) is False:
        return
    if not _TOKEN_RE.fullmatch(token):
        return await screens.ask(
            client, user["_id"],
            "🤔 Itu tidak terlihat seperti token GitHub. Coba lagi —\n"
            "biasanya diawali <code>ghp_</code> atau <code>github_pat_</code>.",
            placeholder="ghp_… / github_pat_…",
        )
    await router.clear_state(user["_id"])
    if await _git_version() < (2, 31):
        return await _screen(
            client, user["_id"],
            "🚫 <b>Git Server Terlalu Tua</b>\n"
            + ui.quote("Update via token butuh <b>git ≥ 2.31</b> di server ini. "
                       "Perbarui git, atau lakukan <code>git pull</code> manual.")
            + "\n🔐 Token tadi sudah dilupakan.",
        )
    # token hanya hidup di variabel lokal ini — tidak ke state/DB/log
    await _check(client, user, token=token)


async def _owner_only(user: dict) -> bool:
    from app.core import users as core_users
    if await core_users.get_role(user["_id"]) != "owner":
        await router.clear_state(user["_id"])
        return False
    return True


# ---------------------------------------------------------------- eksekusi

async def _apply(client, user: dict):
    uid = user["_id"]
    old, _ = await _version()

    rc, n_raw, _ = await _git("rev-list", "--count", "HEAD..FETCH_HEAD", timeout=20)
    if rc != 0 or not utils.parse_int(n_raw):
        return await _screen(
            client, uid,
            "ℹ️ Tidak ada pembaruan siap pasang. Periksa update dulu ya.",
            [[ui.btn("🔄 Periksa Update", "a:up")]],
        )

    _, files, _ = await _git("diff", "--name-only", "HEAD..FETCH_HEAD", timeout=20)
    req_changed = "requirements.txt" in (files or "")

    await screens.show(client, uid, "⏳ <i>Memasang pembaruan…</i>")
    rc, out, err = await _git("merge", "--ff-only", "FETCH_HEAD", timeout=60)
    if rc != 0:
        return await _screen(
            client, uid,
            "❌ <b>Update Gagal</b>\n"
            + ui.quote(utils.esc(utils.take(_redact(err or out), 300)))
            + "\nKemungkinan ada perubahan lokal yang bentrok — "
            "rapikan dulu lewat terminal (<code>git status</code>).",
        )

    new, new_msg = await _version()
    pip_note = ""
    if req_changed:
        await screens.show(client, uid, "📦 <i>Memasang dependensi baru…</i>")
        rc, _, perr = await _run(sys.executable, "-m", "pip", "install", "-q",
                                 "-r", "requirements.txt", timeout=300)
        pip_note = (
            "\n📦 Dependensi terpasang." if rc == 0 else
            f"\n⚠️ <b>pip install gagal</b> — jalankan manual:\n{ui.quote(utils.esc(utils.take(_redact(perr), 200)))}"
        )

    await audit.log("bot_update", actor=uid, frm=old, to=new)
    await _screen(
        client, uid,
        "✅ <b>Update Berhasil!</b>\n"
        + ui.quote(f"📌 <code>{utils.esc(old)}</code> → <code>{utils.esc(new)}</code>\n"
                   f"💬 {utils.esc(utils.take(new_msg, 80))}")
        + pip_note
        + "\n\n🔁 Restart dibutuhkan agar kode baru aktif.",
        [[ui.primary("🔁 Restart Sekarang", "a:rs:yes")]],
        )


@router.cb("a", "rs", owner=True)
async def _cb_restart(client, cq, args, user):
    """Restart bot — fitur berdiri sendiri, tidak harus lewat update."""
    if not args or args[0] != "yes":  # konfirmasi kedua (aksi berbahaya)
        kb = [
            [ui.danger("🔁 Ya, restart sekarang", "a:rs:yes")],
            [ui.btn("◀️ Nanti saja", "a:home")],
        ]
        return await _screen(
            client, user["_id"],
            "⚠️ <b>Konfirmasi Restart</b>\n"
            + ui.quote("Bot offline beberapa detik, lalu kembali dengan kode terkini."),
            kb,
        )
    await _restart(client, user)


async def _restart(client, user: dict):
    uid = user["_id"]
    ver, _ = await _version()
    await db.settings_col.replace_one(
        {"_id": "restart_note"},
        {"_id": "restart_note", "admin_id": uid, "ver": ver, "at": utils.now()},
        upsert=True,
    )
    global _restarting
    if _restarting:  # guard anti-dobel: cegah dua proses anak dari klik ganda
        return
    _restarting = True
    await audit.log("bot_restart", actor=uid, ver=ver)
    await screens.show(
        client, uid,
        "🔁 <b>Restart…</b>\nAku pamit sebentar — beberapa detik lagi kembali "
        "dengan kode baru ✨",
    )
    asyncio.create_task(_restarter())


_restarting = False


def _under_supervisor() -> bool:
    """Terdeteksi dijalankan oleh manajer proses yang akan menghidupkan ulang."""
    return bool(
        os.getenv("INVOCATION_ID")        # systemd
        or os.getenv("SUPERVISOR_ENABLED")  # supervisord
        or os.getenv("PM2_HOME")            # pm2
    )


async def _restarter():
    """Tutup klien dengan rapi (lepas kunci file session), lalu hidupkan ulang.

    Di bawah supervisor (systemd/pm2/supervisord): cukup keluar — manajer proses
    yang menghidupkan ulang dengan bersih (Restart=always). JANGAN spawn proses
    anak: bisa muncul dua instance yang berebut session & auth key.

    Saat dijalankan manual: ganti citra proses DI TEMPAT (os.execv). PID dan
    terminal tetap sama — tidak ada proses lepas yang membuat prompt kembali
    sendiri sementara bot diam-diam berjalan, dan tidak ada tumpang-tindih dua
    instance (penyebab AUTH_KEY_DUPLICATED saat restart)."""
    await asyncio.sleep(1.0)  # beri waktu layar terkirim & callback terjawab
    try:
        await runner.stop()
    except Exception:
        pass
    try:
        await clients.bot.stop()
    except Exception:
        pass
    if _under_supervisor():
        os._exit(0)
    try:
        os.chdir(str(ROOT))
        os.execv(sys.executable, [sys.executable, os.path.abspath(sys.argv[0]), *sys.argv[1:]])
    except Exception:
        log.exception("re-exec gagal — keluar; jalankan ulang manual / andalkan manajer proses")
        os._exit(1)


async def startup_notice(bot):
    """Dipanggil main.py setelah bot online: lapor hasil restart ke admin."""
    note = await db.settings_col.find_one({"_id": "restart_note"})
    if not note:
        return
    await db.settings_col.delete_one({"_id": "restart_note"})
    ver, msg = await _version()
    await screens.notify(
        bot, note["admin_id"],
        "✅ <b>Bot kembali online!</b>\n"
        + ui.quote(f"📌 Versi: <code>{utils.esc(ver)}</code> · "
                   f"{utils.esc(utils.take(msg, 60))}"),
        effect="success",
    )
