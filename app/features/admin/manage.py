"""Admin (Owner): kata terlarang, hashtag, channel menfes, konfigurasi inti."""
from pyrogram.enums import ChatType
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    KeyboardButtonRequestChat,
)

from app import db
from app.core import audit, hashtags, moderation, router, screens, ui, utils
from app.features.admin.panel import BACK as _BACK


# ================================================================ kata terlarang

@router.cb("a", "wd", owner=True)
async def _cb_words(client, cq, args, user):
    sub = args[0] if args else "home"
    if sub == "add":
        await router.set_state(user["_id"], "admin.word_add")
        return await screens.ask(
            client, user["_id"],
            "🧹 <b>Tambah Kata Terlarang</b>\nBalas dengan kata/frasa, satu per baris.\n"
            "Akhiri baris dengan <code>blocked</code> untuk tolak otomatis "
            "(default: <code>suspicious</code> → review).\n"
            "Contoh:\n<code>judol blocked\npinjol</code>",
            placeholder="kata [blocked]",
        )
    if sub == "del":
        await router.set_state(user["_id"], "admin.word_del")
        return await screens.ask(
            client, user["_id"],
            "🗑 <b>Hapus Kata Terlarang</b>\nBalas dengan kata yang ingin dihapus (satu per baris).",
            placeholder="kata",
        )
    await _words_screen(client, user)


async def _words_screen(client, user, note: str = None):
    ws = await moderation.list_words()
    lines = ["🧹 <b>Kata Terlarang</b>", ""]
    if ws:
        lines += [f"• <code>{utils.esc(w['w'])}</code> — {'🚫' if w['sev'] == 'blocked' else '🕵️'}" for w in ws]
        lines += ["", "🚫 = tolak otomatis · 🕵️ = masuk review"]
    else:
        lines.append("<i>Belum ada kata terlarang.</i>")
    if note:
        lines.insert(0, note + "\n")
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Tambah", callback_data="a:wd:add"),
            InlineKeyboardButton("🗑 Hapus", callback_data="a:wd:del"),
        ],
        _BACK,
    ])
    await screens.show(client, user["_id"], "\n".join(lines), kb)


@router.state("admin.word_add")
async def _st_word_add(client, m, user, st):
    await screens.drop(client, m)
    await router.clear_state(user["_id"])
    n = 0
    for line in (m.text or "").splitlines():
        toks = line.strip().split()
        if not toks:
            continue
        sev = moderation.SUSPICIOUS
        if toks[-1].lower() in (moderation.BLOCKED, moderation.SUSPICIOUS):
            sev = toks[-1].lower()
            toks = toks[:-1]
        if toks and await moderation.add_word(" ".join(toks), sev):
            n += 1
    await audit.log("words_add", actor=user["_id"], count=n)
    await _words_screen(client, user, note=f"✅ {n} kata ditambahkan.")


@router.state("admin.word_del")
async def _st_word_del(client, m, user, st):
    await screens.drop(client, m)
    await router.clear_state(user["_id"])
    n = sum([1 for line in (m.text or "").splitlines() if line.strip() and await moderation.remove_word(line)])
    await audit.log("words_del", actor=user["_id"], count=n)
    await _words_screen(client, user, note=f"🗑 {n} kata dihapus.")


# ================================================================ hashtag

@router.cb("a", "ht", owner=True)
async def _cb_hashtags(client, cq, args, user):
    sub = args[0] if args else "home"
    if sub == "add":
        await router.set_state(user["_id"], "admin.ht_add")
        return await screens.ask(
            client, user["_id"],
            "#️⃣ <b>Tambah Hashtag</b>\nBalas dengan format <code>tag emoji</code>, "
            "boleh banyak baris.\nContoh:\n<code>curhat 🫂\nkampus 🎓</code>",
            placeholder="tag emoji",
        )
    if sub == "del":
        await router.set_state(user["_id"], "admin.ht_del")
        return await screens.ask(
            client, user["_id"],
            "🗑 <b>Hapus Hashtag</b>\nBalas dengan hashtag yang ingin dihapus (satu per baris).",
            placeholder="tag",
        )
    if sub == "tg" and len(args) > 1:
        await hashtags.toggle(args[1])
        return await _ht_screen(client, user, note=f"🔁 #{args[1]} dialihkan.")
    await _ht_screen(client, user)


async def _ht_screen(client, user, note: str = None):
    tags = await hashtags.all_tags(active_only=False)
    lines = ["#️⃣ <b>Hashtag</b> — ketuk untuk aktif/nonaktif", ""]
    rows = []
    for t in tags[:24]:
        ic = "✅" if t.get("active") else "💤"
        rows.append([InlineKeyboardButton(
            f"{ic} {t.get('emoji', '')}#{t['tag']} ({t.get('uses', 0)}×)",
            callback_data=f"a:ht:tg:{t['tag']}",
        )])
    if not tags:
        lines.append("<i>Belum ada hashtag.</i>")
    if note:
        lines.insert(0, note + "\n")
    rows.append([
        InlineKeyboardButton("➕ Tambah", callback_data="a:ht:add"),
        InlineKeyboardButton("🗑 Hapus", callback_data="a:ht:del"),
    ])
    rows.append(_BACK)
    await screens.show(client, user["_id"], "\n".join(lines), InlineKeyboardMarkup(rows))


@router.state("admin.ht_add")
async def _st_ht_add(client, m, user, st):
    await screens.drop(client, m)
    await router.clear_state(user["_id"])
    n = 0
    for line in (m.text or "").splitlines():
        toks = line.strip().split()
        if not toks:
            continue
        emoji = toks[1] if len(toks) > 1 else ""
        if await hashtags.add(toks[0], emoji):
            n += 1
    await _ht_screen(client, user, note=f"✅ {n} hashtag ditambahkan.")


@router.state("admin.ht_del")
async def _st_ht_del(client, m, user, st):
    await screens.drop(client, m)
    await router.clear_state(user["_id"])
    n = sum([1 for line in (m.text or "").splitlines() if line.strip() and await hashtags.remove(line)])
    await _ht_screen(client, user, note=f"🗑 {n} hashtag dihapus.")


# ================================================================ mood

@router.cb("a", "md", owner=True)
async def _cb_moods(client, cq, args, user):
    sub = args[0] if args else "home"
    if sub == "add":
        await router.set_state(user["_id"], "admin.mood_add")
        return await screens.ask(
            client, user["_id"],
            "🎭 <b>Tambah / Ubah Mood</b>\n"
            "Format <code>key emoji Label</code>, boleh banyak baris.\n"
            "Contoh:\n<code>galau 😔 Galau\nsemangat 🔥 Semangat</code>",
            placeholder="key emoji Label",
        )
    if sub == "del":
        await router.set_state(user["_id"], "admin.mood_del")
        return await screens.ask(
            client, user["_id"],
            "🗑 <b>Hapus Mood</b>\nBalas dengan <code>key</code> mood (satu per baris).",
            placeholder="key",
        )
    await _md_screen(client, user)


async def _md_screen(client, user, note: str = None):
    s = await db.get_settings(fresh=True)
    moods = s.get("moods", [])
    lines = ["🎭 <b>Mood</b> — vibe pilihan saat menulis menfes", ""]
    if moods:
        lines += [
            f"• <code>{utils.esc(mo['key'])}</code> {mo.get('emoji', '')} {utils.esc(mo.get('label', ''))}"
            for mo in moods
        ]
    else:
        lines.append("<i>Belum ada mood.</i>")
    if note:
        lines.insert(0, note + "\n")
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Tambah", callback_data="a:md:add"),
            InlineKeyboardButton("🗑 Hapus", callback_data="a:md:del"),
        ],
        _BACK,
    ])
    await screens.show(client, user["_id"], "\n".join(lines), kb)


@router.state("admin.mood_add")
async def _st_mood_add(client, m, user, st):
    await screens.drop(client, m)
    await router.clear_state(user["_id"])
    s = await db.get_settings(fresh=True)
    moods = list(s.get("moods", []))
    by_key = {mo["key"]: mo for mo in moods}
    n = 0
    for line in (m.text or "").splitlines():
        toks = line.strip().split()
        if len(toks) < 2:
            continue
        key = toks[0].lower()
        emoji = toks[1]
        label = " ".join(toks[2:]) or key.capitalize()
        if key in by_key:
            by_key[key].update(emoji=emoji, label=label)
        else:
            new = {"key": key, "emoji": emoji, "label": label}
            moods.append(new)
            by_key[key] = new
        n += 1
    await db.update_settings(moods=moods)
    await audit.log("moods_add", actor=user["_id"], count=n)
    await _md_screen(client, user, note=f"✅ {n} mood disimpan.")


@router.state("admin.mood_del")
async def _st_mood_del(client, m, user, st):
    await screens.drop(client, m)
    await router.clear_state(user["_id"])
    s = await db.get_settings(fresh=True)
    moods = s.get("moods", [])
    dels = {t.strip().lower() for t in (m.text or "").splitlines() if t.strip()}
    kept = [mo for mo in moods if mo["key"] not in dels]
    removed = len(moods) - len(kept)
    await db.update_settings(moods=kept)
    await audit.log("moods_del", actor=user["_id"], count=removed)
    await _md_screen(client, user, note=f"🗑 {removed} mood dihapus.")


# ================================================================ channel menfes

@router.cb("a", "ch", owner=True)
async def _cb_channel(client, cq, args, user):
    sub = args[0] if args else "home"
    s = await db.get_settings(fresh=True)
    if sub == "set":
        await router.set_state(user["_id"], "admin.channel")
        # tombol pilih channel memakai request_chat (CLAUDE.md §11 & §14)
        picker_btn = KeyboardButton(
            "📢 Pilih Channel…",
            request_chat=KeyboardButtonRequestChat(
                button_id=7,
                chat_is_channel=True,
                request_title=True,
                request_username=True,
            ),
        )
        return await screens.ask(
            client, user["_id"],
            "📢 <b>Atur Channel Menfes</b>\n"
            "Ketuk <b>📢 Pilih Channel…</b> di bawah untuk memilih lewat Telegram, "
            "atau balas dengan:\n"
            "• <code>@usernamechannel</code> / ID <code>-100…</code> / link\n"
            "• <b>forward</b> satu postingan dari channel-nya.\n\n"
            "Bot harus jadi admin dengan hak kirim pesan.",
            placeholder="@channel · -100… · forward",
            choices=[[picker_btn]],
        )
    if sub == "test":
        ok, err = await _test_channel(client, s.get("channel_id"))
        note = "✅ Tes kirim berhasil!" if ok else f"❌ Gagal: {utils.esc(err)}"
        return await _ch_screen(client, user, note=note)
    if sub == "unset":
        if len(args) > 1 and args[1] == "yes":
            await db.update_settings(channel_id=None, channel_title=None)
            return await _ch_screen(client, user, note="❌ Channel dilepas.")
        kb = InlineKeyboardMarkup([
            [ui.danger("❌ Yakin, lepas channel", "a:ch:unset:yes")],
            [InlineKeyboardButton("◀️ Batal", callback_data="a:ch")],
        ])
        return await screens.show(client, user["_id"], "Lepas channel menfes?", kb)
    await _ch_screen(client, user)


async def _ch_screen(client, user, note: str = None):
    s = await db.get_settings(fresh=True)
    cur = (
        f"<b>{utils.esc(s.get('channel_title') or '?')}</b> (<code>{s['channel_id']}</code>)"
        if s.get("channel_id") else "<i>belum diatur</i>"
    )
    text = f"📢 <b>Channel Menfes</b>\nSaat ini: {cur}"
    if note:
        text = f"{note}\n\n{text}"
    rows = [[InlineKeyboardButton("✏️ Atur Channel", callback_data="a:ch:set")]]
    if s.get("channel_id"):
        rows.append([
            InlineKeyboardButton("🧪 Tes Kirim", callback_data="a:ch:test"),
            InlineKeyboardButton("❌ Lepas", callback_data="a:ch:unset"),
        ])
    rows.append(_BACK)
    await screens.show(client, user["_id"], text, InlineKeyboardMarkup(rows))


@router.state("admin.channel")
async def _st_channel(client, m, user, st):
    chat = None
    if m.chat_shared:  # hasil tombol request_chat (data chat shared)
        shared = m.chat_shared.chat
        cid = getattr(shared, "id", None)
        try:
            chat = await client.get_chat(cid)
        except Exception:
            chat = shared if cid else None
    elif m.forward_from_chat:
        chat = m.forward_from_chat
    else:
        raw = (m.text or "").strip()
        raw = raw.replace("https://", "").replace("http://", "")
        if raw.lower().startswith("t.me/"):
            raw = raw[5:].strip("/")
        raw = raw.lstrip("@").split("?")[0]
        try:
            chat = await client.get_chat(utils.parse_int(raw) or raw)
        except Exception:
            chat = None
    await screens.drop(client, m)
    if not chat or getattr(chat, "type", ChatType.CHANNEL) != ChatType.CHANNEL:
        return await screens.show(
            client, user["_id"], "🔍 Itu bukan channel. Coba lagi:",
            InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Batal", callback_data="a:ch")]]),
        )
    ok, err = await _test_channel(client, chat.id)
    if not ok:
        return await screens.show(
            client, user["_id"],
            f"❌ Bot belum bisa kirim ke <b>{utils.esc(chat.title)}</b>: {utils.esc(err)}\n"
            "Jadikan bot admin dengan hak kirim, lalu coba lagi.",
            InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Batal", callback_data="a:ch")]]),
        )
    await router.clear_state(user["_id"])
    await db.update_settings(channel_id=chat.id, channel_title=chat.title)
    await audit.log("channel_set", actor=user["_id"], target=chat.id)
    await _ch_screen(client, user, note=f"✅ Channel <b>{utils.esc(chat.title)}</b> terhubung!")


async def _test_channel(client, channel_id) -> tuple[bool, str]:
    if not channel_id:
        return False, "channel belum diatur"
    try:
        msg = await client.send_message(channel_id, "🧪 Tes koneksi bot menfes — abaikan.")
        try:
            await msg.delete()
        except Exception:
            pass
        return True, ""
    except Exception as e:
        return False, type(e).__name__


# ================================================================ konfigurasi inti

_TOGGLES = [
    ("menfes_enabled", "✉️ Menfes"),
    ("confes_enabled", "💌 Confes"),
    ("paused", "⏸️ Jeda Pengiriman"),
    ("assistant_enabled", "🤖 Akun Asisten"),
]
_NUMBERS = [
    ("daily_points", "⚡ Poin harian"),
    ("menfes_cooldown", "⏱ Cooldown menfes (dtk)"),
    ("confes_cooldown", "⏱ Cooldown confes (dtk)"),
    ("max_pending_per_target", "📪 Maks pending/target"),
]


@router.cb("a", "cf", owner=True)
async def _cb_config(client, cq, args, user):
    sub = args[0] if args else "home"
    if sub == "t" and len(args) > 1:
        s = await db.get_settings(fresh=True)
        key = args[1]
        if key in dict(_TOGGLES):
            new = not s.get(key)
            await db.update_settings(**{key: new})
            await audit.log("config_toggle", actor=user["_id"], target=key)
            if key == "assistant_enabled" and new:  # asisten dinyalakan → susul confes tertunda
                import asyncio

                from app.core import delivery
                asyncio.create_task(delivery.retry_pending_warnings(client))
        return await _cf_screen(client, user, note="👍 Tersimpan.")
    if sub == "n" and len(args) > 1:
        await router.set_state(user["_id"], "admin.cfg_num", key=args[1])
        label = dict(_NUMBERS).get(args[1], args[1])
        return await screens.ask(
            client, user["_id"], f"🔢 Balas dengan nilai baru untuk <b>{label}</b>.",
            placeholder="angka ≥ 0",
        )
    await _cf_screen(client, user)


async def _cf_screen(client, user, note: str = None):
    s = await db.get_settings(fresh=True)
    text = "⚙️ <b>Konfigurasi Inti</b>\nKetuk untuk mengubah."
    if note:
        text = f"{note}\n\n{text}"
    rows = []
    for key, label in _TOGGLES:
        on = s.get(key)
        rows.append([InlineKeyboardButton(
            f"{label}: {'🟢 ON' if on else '🔴 OFF'}", callback_data=f"a:cf:t:{key}"
        )])
    for key, label in _NUMBERS:
        rows.append([InlineKeyboardButton(
            f"{label}: {s.get(key)}", callback_data=f"a:cf:n:{key}"
        )])
    rows.append(_BACK)
    await screens.show(client, user["_id"], text, InlineKeyboardMarkup(rows))


@router.state("admin.cfg_num")
async def _st_cfg_num(client, m, user, st):
    await screens.drop(client, m)
    key = st["data"].get("key")
    val = utils.parse_int(m.text)
    if val is None or val < 0 or key not in dict(_NUMBERS):
        return await _cf_screen(client, user, note="⚠️ Harus angka ≥ 0. Coba lagi.")
    await router.clear_state(user["_id"])
    await db.update_settings(**{key: val})
    await audit.log("config_num", actor=user["_id"], target=key, value=val)
    await _cf_screen(client, user, note="👍 Tersimpan.")
