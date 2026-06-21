"""Mesin draft & preview — pusat pengalaman kirim pesan (CLAUDE.md §10).

Satu mesin untuk semua kind: menfes | confes | reply | bcast.
Alur: begin → input konten → PREVIEW (bentuk akhir + meta + biaya) →
Kirim / Edit / Batal. Moderasi final terjadi pada versi terakhir saat Kirim.
Draf kedaluwarsa otomatis lewat TTL Mongo (expire_at).
"""
import logging
from datetime import timedelta

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app import db
from app.core import audit, content as content_mod
from app.core import delivery, hashtags, moderation, ratelimit, router, screens, ui, utils, wallet
from app.core import users as core_users

log = logging.getLogger("drafts")

EXPIRE_MIN = 30
KIND_LABEL = {"menfes": "MENFES", "confes": "CONFES", "reply": "BALASAN ANONIM", "bcast": "BROADCAST"}
_COOLDOWN_KIND = {"menfes": "menfes", "confes": "confes", "reply": "reply"}


def home_kb(note_rows: list = None) -> InlineKeyboardMarkup:
    rows = list(note_rows or [])
    rows.append([InlineKeyboardButton("🏠 Menu Utama", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------- lifecycle

async def begin(uid: int, kind: str, **extra) -> dict:
    await db.drafts.update_many(
        {"user_id": uid, "status": "draft"}, {"$set": {"status": "cancelled"}}
    )
    doc = {
        "user_id": uid, "kind": kind, "status": "draft",
        "content": None, "tags": [], "mood": None, "target": None,
        "created_at": utils.now(),
        "expire_at": utils.now() + timedelta(minutes=EXPIRE_MIN),
        **extra,
    }
    res = await db.drafts.insert_one(doc)
    doc["_id"] = res.inserted_id
    return doc


async def get_active(did, uid: int = None) -> dict | None:
    o = utils.oid(did)
    if not o:
        return None
    d = await db.drafts.find_one({"_id": o, "status": "draft"})
    if d and uid is not None and d["user_id"] != uid:
        return None
    return d


async def active_or_expire(client, m, user: dict, st: dict) -> dict | None:
    """Hapus pesan input pengguna, lalu ambil draf aktif dari state.

    Bila draf sudah kedaluwarsa: bersihkan state + tampilkan menu utama, dan
    kembalikan None (pemanggil cukup `if not draft: return`). Menyatukan blok
    'draf kedaluwarsa' yang dulu tersebar di banyak state handler (CLAUDE.md §3)."""
    await screens.drop(client, m)
    draft = await get_active((st.get("data") or {}).get("draft_id"), user["_id"])
    if draft:
        return draft
    await router.clear_state(user["_id"])
    from app.features import start
    await start.show_main(client, user, note="⌛ Draf sudah kedaluwarsa.")
    return None


async def patch(did, **fields):
    fields["expire_at"] = utils.now() + timedelta(minutes=EXPIRE_MIN)
    await db.drafts.update_one({"_id": utils.oid(did)}, {"$set": fields})


async def set_status(did, status: str):
    await db.drafts.update_one({"_id": utils.oid(did)}, {"$set": {"status": status}})


# ---------------------------------------------------------------- layar

async def prompt_content(client, user: dict, draft: dict, note: str = None):
    kind = draft["kind"]
    head = {
        "menfes": "✍️ <b>Tulis Menfes-mu</b>",
        "confes": f"✍️ <b>Tulis Confes untuk {utils.esc((draft.get('target') or {}).get('name', '…'))}</b>",
        "reply": "✍️ <b>Tulis Balasan Anonim</b>",
        "bcast": "✍️ <b>Konten Broadcast</b>",
    }[kind]
    ph = {
        "menfes": "Tulis menfes-mu di sini…",
        "confes": "Tulis confes-mu di sini…",
        "reply": "Tulis balasan anonimmu…",
        "bcast": "Tulis konten broadcast…",
    }[kind]
    text = (
        f"{head}\n\n"
        "Boleh teks, foto, video, GIF, audio, voice, atau file.\n"
        + ui.muted("Kamu akan lihat preview dulu — tidak ada yang terkirim "
                   "sebelum kamu menekan Kirim.")
    )
    if note:
        text = f"{note}\n\n{text}"
    await router.set_state(user["_id"], "draft.content", draft_id=str(draft["_id"]))
    await screens.ask(client, user["_id"], text, placeholder=ph)


async def show_preview(client, user: dict, draft: dict):
    s = await db.get_settings()
    kind = draft["kind"]
    info = []
    if kind == "confes":
        info.append(f"Untuk: <b>{utils.esc((draft.get('target') or {}).get('name', '?'))}</b> · gratis")
    if draft.get("mood"):
        info.append(f"Mood: {draft['mood']['emoji']} {utils.esc(draft['mood']['label'])}")
    if draft.get("tags"):
        dmap = await hashtags.docs_map(draft["tags"])
        info.append(f"Hashtag: {utils.esc(hashtags.render(draft['tags'], dmap))}")
    if kind == "menfes":
        fresh = await core_users.get(user["_id"]) or user
        _, label = wallet.quote(fresh, s)
        info.append(f"Biaya: {label}")
    if kind == "bcast":
        n = await db.users.count_documents({"banned": False})
        info.append(f"Jalur: <b>{draft.get('audience', 'bot')}</b> · ±{n} pengguna")
    meta = [
        ui.title(f"Preview {KIND_LABEL[kind].capitalize()}", "👀"),
        ui.quote("\n".join(info)) if info else "",
        ui.muted("Persis seperti ini yang akan terkirim — belum ada yang dikirim. "
                 "Kirim konten baru untuk mengganti isi."),
    ]
    kb = await _preview_kb(draft)
    await router.set_state(user["_id"], "draft.preview", draft_id=str(draft["_id"]))
    await screens.show(client, user["_id"], "\n".join(meta), kb, content=draft["content"])


async def _preview_kb(draft: dict) -> InlineKeyboardMarkup:
    did = str(draft["_id"])
    rows = []
    if draft["kind"] == "menfes" and not draft.get("tags"):
        sugg = await hashtags.suggest(content_mod.body(draft["content"] or {}), 3)
        if sugg:
            rows.append([
                InlineKeyboardButton(
                    f"✨ {t.get('emoji', '')}#{t['tag']}".strip(),
                    callback_data=f"d:tag:{did}:{t['tag']}",
                ) for t in sugg
            ])
    rows.append([
        ui.success("🚀 Kirim", f"d:send:{did}"),
        ui.btn("✏️ Edit", f"d:edit:{did}"),
    ])
    rows.append([ui.danger("🗑 Batal", f"d:cancel:{did}")])
    return InlineKeyboardMarkup(rows)


async def _show_edit_menu(client, user: dict, draft: dict):
    did = str(draft["_id"])
    rows = [[InlineKeyboardButton("📝 Ganti Isi", callback_data=f"d:part:{did}:isi")]]
    if draft["kind"] == "menfes":
        rows.append([
            InlineKeyboardButton("#️⃣ Hashtag", callback_data=f"d:part:{did}:tag"),
            InlineKeyboardButton("🎭 Mood", callback_data=f"d:part:{did}:mood"),
        ])
    if draft["kind"] == "confes":
        rows.append([InlineKeyboardButton("🎯 Ganti Target", callback_data=f"d:part:{did}:tgt")])
    rows.append([
        InlineKeyboardButton("👀 Kembali ke Preview", callback_data=f"d:prev:{did}"),
        InlineKeyboardButton("🗑 Batal", callback_data=f"d:cancel:{did}"),
    ])
    await screens.show(
        client, user["_id"],
        "✏️ <b>Edit Draf</b>\nBagian mana yang ingin kamu perbaiki?",
        InlineKeyboardMarkup(rows),
    )


# ---------------------------------------------------------------- states

@router.state("draft.content")
async def _st_content(client, m, user, st):
    await _consume_content(client, m, user, st)


@router.state("draft.preview")
async def _st_preview_quickedit(client, m, user, st):
    """Modern: saat preview, kirim konten baru = langsung mengganti isi draf."""
    await _consume_content(client, m, user, st)


async def _consume_content(client, m, user, st):
    draft = await active_or_expire(client, m, user, st)
    if not draft:
        return
    c = content_mod.extract(m)
    if not c:
        return await prompt_content(
            client, user, draft, note="🙈 Jenis pesan itu belum didukung."
        )
    s = await db.get_settings()
    err = content_mod.check_limits(c, s)
    if err:
        return await prompt_content(client, user, draft, note=f"⚠️ {err}")
    await patch(draft["_id"], content=c)
    draft["content"] = c
    await show_preview(client, user, draft)


@router.state("draft.tags")
async def _st_tags(client, m, user, st):
    draft = await active_or_expire(client, m, user, st)
    if not draft:
        return
    s = await db.get_settings()
    raw = (m.text or "").replace(",", " ").split()
    tags, err = await hashtags.validate(raw, s)
    if err:
        return await _tag_screen(client, user, draft, note=f"⚠️ {err}")
    await patch(draft["_id"], tags=tags)
    draft["tags"] = tags
    await show_preview(client, user, draft)


async def _tag_screen(client, user, draft, note: str = None):
    did = str(draft["_id"])
    s = await db.get_settings()
    chosen = draft.get("tags") or []
    rows, row = [], []
    for t in await hashtags.all_tags():
        mark = "✅" if t["tag"] in chosen else t.get("emoji") or "▫️"
        row.append(InlineKeyboardButton(f"{mark}#{t['tag']}", callback_data=f"d:tag:{did}:{t['tag']}"))
        if len(row) == 3:
            rows.append(row)
            row = []
        if len(rows) >= 4:
            break
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton("✍️ Ketik Sendiri", callback_data=f"d:tagin:{did}"),
        InlineKeyboardButton("🚫 Tanpa Hashtag", callback_data=f"d:notag:{did}"),
    ])
    rows.append([InlineKeyboardButton("👀 Preview", callback_data=f"d:prev:{did}")])
    text = (
        "#️⃣ <b>Hashtag</b> <i>(opsional)</i>\n"
        f"Pilih dari daftar, atau ketik sendiri (maks {s['max_hashtags']}).\n"
        f"Terpilih: {utils.esc(' '.join('#' + t for t in chosen)) or '—'}"
    )
    if note:
        text = f"{note}\n\n{text}"
    await router.clear_state(user["_id"])
    await screens.show(client, user["_id"], text, InlineKeyboardMarkup(rows))


async def _mood_screen(client, user, draft):
    did = str(draft["_id"])
    s = await db.get_settings()
    rows, row = [], []
    cur = (draft.get("mood") or {}).get("key")
    for mo in s["moods"]:
        mark = "✅ " if mo["key"] == cur else ""
        row.append(InlineKeyboardButton(
            f"{mark}{mo['emoji']} {mo['label']}", callback_data=f"d:mood:{did}:{mo['key']}"
        ))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("🚫 Tanpa Mood", callback_data=f"d:mood:{did}:-")])
    rows.append([InlineKeyboardButton("👀 Preview", callback_data=f"d:prev:{did}")])
    await screens.show(
        client, user["_id"],
        "🎭 <b>Mood Pesan</b> <i>(opsional)</i>\nBiar pembaca tahu vibe-nya 😎",
        InlineKeyboardMarkup(rows),
    )


# ---------------------------------------------------------------- callbacks

@router.cb("d", "prev")
async def _cb_prev(client, cq, args, user):
    draft = await get_active(args[0], user["_id"])
    if not draft:
        return await cq.answer("⌛ Draf sudah tidak aktif.", show_alert=True)
    await show_preview(client, user, draft)


@router.cb("d", "edit")
async def _cb_edit(client, cq, args, user):
    draft = await get_active(args[0], user["_id"])
    if not draft:
        return await cq.answer("⌛ Draf sudah tidak aktif.", show_alert=True)
    await router.clear_state(user["_id"])
    await _show_edit_menu(client, user, draft)


@router.cb("d", "cancel")
async def _cb_cancel(client, cq, args, user):
    draft = await get_active(args[0], user["_id"])
    if draft:
        await set_status(draft["_id"], "cancelled")
    await router.clear_state(user["_id"])
    from app.features import start
    await start.show_main(client, user, note="🗑 Draf dibatalkan. Santai, tidak ada yang terkirim.")


@router.cb("d", "part")
async def _cb_part(client, cq, args, user):
    draft = await get_active(args[0], user["_id"])
    if not draft:
        return await cq.answer("⌛ Draf sudah tidak aktif.", show_alert=True)
    part = args[1] if len(args) > 1 else "isi"
    if part == "isi":
        await prompt_content(client, user, draft)
    elif part == "tag":
        await _tag_screen(client, user, draft)
    elif part == "mood":
        await _mood_screen(client, user, draft)
    elif part == "tgt":
        from app.features import confes
        await confes.prompt_target(client, user, draft)


@router.cb("d", "tag")
async def _cb_tag_toggle(client, cq, args, user):
    draft = await get_active(args[0], user["_id"])
    if not draft:
        return await cq.answer("⌛ Draf sudah tidak aktif.", show_alert=True)
    tag = args[1]
    s = await db.get_settings()
    tags = list(draft.get("tags") or [])
    if tag in tags:
        tags.remove(tag)
    elif len(tags) >= s["max_hashtags"]:
        return await cq.answer(f"Maksimal {s['max_hashtags']} hashtag.", show_alert=True)
    else:
        tags.append(tag)
    await patch(draft["_id"], tags=tags)
    draft["tags"] = tags
    await show_preview(client, user, draft)


@router.cb("d", "tagin")
async def _cb_tag_input(client, cq, args, user):
    draft = await get_active(args[0], user["_id"])
    if not draft:
        return await cq.answer("⌛ Draf sudah tidak aktif.", show_alert=True)
    await router.set_state(user["_id"], "draft.tags", draft_id=str(draft["_id"]))
    await screens.ask(
        client, user["_id"],
        "✍️ Ketik hashtag-mu, pisahkan spasi.\nContoh: <code>#curhat #kampus</code>",
        placeholder="#curhat #kampus",
    )


@router.cb("d", "notag")
async def _cb_notag(client, cq, args, user):
    draft = await get_active(args[0], user["_id"])
    if not draft:
        return await cq.answer("⌛ Draf sudah tidak aktif.", show_alert=True)
    await patch(draft["_id"], tags=[])
    draft["tags"] = []
    await show_preview(client, user, draft)


@router.cb("d", "mood")
async def _cb_mood(client, cq, args, user):
    draft = await get_active(args[0], user["_id"])
    if not draft:
        return await cq.answer("⌛ Draf sudah tidak aktif.", show_alert=True)
    key = args[1]
    mood = None
    if key != "-":
        s = await db.get_settings()
        mood = next((m for m in s["moods"] if m["key"] == key), None)
    await patch(draft["_id"], mood=mood)
    draft["mood"] = mood
    await show_preview(client, user, draft)


@router.cb("d", "send")
async def _cb_send(client, cq, args, user):
    draft = await get_active(args[0], user["_id"])
    if not draft or not draft.get("content"):
        return await cq.answer("⌛ Draf sudah tidak aktif.", show_alert=True)
    # broadcast = aksi berbahaya → wajib konfirmasi kedua
    if draft["kind"] == "bcast" and (len(args) < 2 or args[1] != "yes"):
        did = str(draft["_id"])
        kb = InlineKeyboardMarkup([
            [ui.danger("📣 Ya, kirim broadcast sekarang", f"d:send:{did}:yes")],
            [ui.btn("◀️ Kembali ke preview", f"d:prev:{did}")],
        ])
        return await screens.show(
            client, user["_id"],
            "📣 <b>Konfirmasi Broadcast</b>\n"
            + ui.quote("Pesan akan dikirim massal ke semua pengguna.\nAksi ini tidak bisa dibatalkan."),
            kb,
        )
    if not await ratelimit.lock(f"send:{draft['_id']}", 60):
        return await cq.answer("⏳ Sedang diproses, sabar ya…")
    try:
        await _submit(client, user, draft)
    finally:
        await ratelimit.unlock(f"send:{draft['_id']}")


# ---------------------------------------------------------------- pipeline

async def _submit(client, user: dict, draft: dict):
    """Pemeriksaan final saat tombol Kirim ditekan (versi terakhir draf)."""
    uid = user["_id"]
    s = await db.get_settings(fresh=True)
    kind = draft["kind"]

    if s["paused"] and kind != "bcast":
        return await screens.show(
            client, uid, "⏸️ Bot sedang jeda sementara. Draf-mu tetap aman, coba lagi nanti.",
            await _preview_kb(draft),
        )
    if kind == "menfes" and not s["menfes_enabled"]:
        return await screens.show(client, uid, "😴 Fitur menfes sedang dimatikan admin.", home_kb())
    if kind in ("confes", "reply") and not s["confes_enabled"]:
        return await screens.show(client, uid, "😴 Fitur confes sedang dimatikan admin.", home_kb())

    cd_kind = _COOLDOWN_KIND.get(kind)
    if cd_kind:
        left = ratelimit.cooldown_left(user, cd_kind, s)
        if left:
            return await screens.show(
                client, uid,
                f"⏳ Sabar dulu ya — tunggu <b>{utils.left_str(left)}</b> lagi sebelum mengirim.",
                await _preview_kb(draft),
            )

    level, hits = await moderation.check(
        content_mod.body(draft["content"]), " ".join(draft.get("tags") or [])
    )
    if level == moderation.BLOCKED:
        await set_status(draft["_id"], "rejected")
        await router.clear_state(uid)
        await audit.strike(uid, "blocked")
        await audit.log("auto_blocked", actor=uid, target=str(draft["_id"]), hits=hits, kind=kind)
        return await screens.show(
            client, uid,
            "🚫 <b>Pesan ditolak otomatis</b>\nKonten mengandung kata yang dilarang.\n"
            "💡 Saldo poin/coin-mu <b>tidak terpotong</b>.",
            home_kb(),
        )
    if level == moderation.SUSPICIOUS:
        await set_status(draft["_id"], "review")
        await router.clear_state(uid)
        await audit.log("to_review", actor=uid, target=str(draft["_id"]), hits=hits, kind=kind)
        from app.features.admin import panel
        await panel.ping_review(client)
        return await screens.show(
            client, uid,
            "🕵️ <b>Masuk antrean review</b>\nPesanmu mengandung kata yang perlu dicek admin "
            "dulu. Kamu akan diberi tahu hasilnya.\n"
            "💡 Saldo hanya terpotong jika disetujui <i>dan</i> terkirim.",
            home_kb(),
        )

    await finalize(client, user, draft, s)


async def finalize(client, user: dict, draft: dict, s: dict, by_admin: int = None):
    """Eksekusi pengiriman + pemotongan saldo (idempoten). Dipakai juga oleh
    persetujuan review admin. -> (ok, info)."""
    uid = draft["user_id"]
    did = str(draft["_id"])
    kind = draft["kind"]

    if kind == "menfes":
        fresh = await core_users.get(uid) or user
        method, label = wallet.quote(fresh, s)
        if not method:
            await router.clear_state(uid)
            kb = home_kb([[
                InlineKeyboardButton("🏆 Referral", callback_data="p:ref"),
                InlineKeyboardButton("🎁 Redeem", callback_data="p:redeem"),
            ]])
            await screens.show(
                client, uid,
                "😬 <b>Saldo tidak cukup</b>\nPoin harian habis dan coin kosong.\n"
                "Ajak teman lewat referral atau redeem voucher untuk dapat coin! 🪙",
                kb,
            )
            return False, "saldo"
        ok, link, err = await delivery.post_menfes(client, draft, s)
        if not ok:
            await set_status(did, "failed")
            await router.clear_state(uid)
            await screens.show(
                client, uid,
                f"❌ <b>Gagal mengirim</b>: {utils.esc(err)}\n💡 Saldo-mu aman, tidak terpotong.",
                home_kb(),
            )
            return False, err
        await wallet.commit(uid, method, ref=f"menfes:{did}", note="kirim menfes")
        await set_status(did, "sent")
        await ratelimit.mark(uid, "menfes")
        await db.users.update_one({"_id": uid}, {"$inc": {"stats.menfes": 1}})
        await hashtags.bump(draft.get("tags") or [])
        await router.clear_state(uid)
        rows = [[InlineKeyboardButton("🔗 Lihat di Channel", url=link)]] if link else []
        rows.append([InlineKeyboardButton("✉️ Kirim Lagi", callback_data="m:new")])
        note = " (disetujui admin)" if by_admin else ""
        await screens.show(
            client, uid,
            f"🚀 <b>Menfes terkirim!</b>{note}\nIdentitasmu tetap rahasia 🤫",
            home_kb(rows), effect="sent",
        )
        return True, link

    if kind == "confes":
        sender = await core_users.get(uid) or user
        code, text, eff = await delivery.queue_confes(client, sender, draft, s)
        await set_status(did, "sent" if code in ("delivered", "pending") else "stopped")
        if code in ("delivered", "pending"):
            await ratelimit.mark(uid, "confes")
            await db.users.update_one({"_id": uid}, {"$inc": {"stats.confes": 1}})
        await router.clear_state(uid)
        await screens.show(client, uid, text, home_kb(), effect=eff)
        return code != "stopped", text

    if kind == "reply":
        thread = await db.threads.find_one({"_id": utils.oid(draft.get("thread_id"))})
        if not thread:
            await set_status(did, "failed")
            await router.clear_state(uid)
            await screens.show(client, uid, "⌛ Thread sudah tidak tersedia.", home_kb())
            return False, "thread"
        ok, err = await delivery.relay_reply(client, thread, uid, draft["content"])
        await set_status(did, "sent" if ok else "failed")
        await router.clear_state(uid)
        if ok:
            await ratelimit.mark(uid, "reply")
            await db.users.update_one({"_id": uid}, {"$inc": {"stats.replies": 1}})
            await screens.show(
                client, uid, "💬 <b>Balasan anonim terkirim!</b> 🤫", home_kb(), effect="sent"
            )
        else:
            await screens.show(client, uid, f"❌ {utils.esc(err)}", home_kb())
        return ok, err

    if kind == "bcast":
        role = await core_users.get_role(uid)
        if role != "owner":
            return False, "bukan owner"
        from app.features.admin import broadcast
        await set_status(did, "sent")
        await router.clear_state(uid)
        await broadcast.run(client, draft)
        return True, "berjalan"

    return False, "kind tidak dikenal"
