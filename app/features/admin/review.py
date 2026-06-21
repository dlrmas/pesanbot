"""Admin (Owner+Moderator): antrean review konten `suspicious` + laporan pengguna."""
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app import db
from app.core import audit, content as content_mod
from app.core import drafts, moderation, ratelimit, router, screens, ui, utils
from app.core import users as core_users
from app.features.admin.panel import BACK as _BACK


@router.cb("a", "rv", admin=True)
async def _cb_review(client, cq, args, user):
    sub = args[0] if args else "home"

    if sub == "q":
        return await _show_next_draft(client, user)

    if sub == "ok":
        d = await db.drafts.find_one({"_id": utils.oid(args[1]), "status": "review"})
        if not d:
            return await _show_next_draft(client, user, note="ℹ️ Item sudah ditangani admin lain.")
        if not await ratelimit.lock(f"review:{args[1]}", 30):
            return await cq.answer("⏳ Sedang diproses admin lain…")
        s = await db.get_settings(fresh=True)
        owner_user = await core_users.get(d["user_id"]) or {"_id": d["user_id"]}
        ok, info = await drafts.finalize(client, owner_user, d, s, by_admin=user["_id"])
        await audit.log("review_approve", actor=user["_id"], target=str(d["_id"]), ok=ok)
        note = "✅ Disetujui & dikirim." if ok else f"⚠️ Disetujui tapi gagal kirim: {utils.esc(str(info))}"
        return await _show_next_draft(client, user, note=note)

    if sub in ("no", "nos"):
        d = await db.drafts.find_one({"_id": utils.oid(args[1]), "status": "review"})
        if not d:
            return await _show_next_draft(client, user, note="ℹ️ Item sudah ditangani admin lain.")
        await db.drafts.update_one({"_id": d["_id"]}, {"$set": {"status": "rejected"}})
        if sub == "nos":
            await audit.strike(d["user_id"], "strikes")
        await audit.log("review_reject", actor=user["_id"], target=str(d["_id"]), strike=sub == "nos")
        await screens.notify(
            client, d["user_id"],
            "🚫 <b>Pesanmu tidak lolos review admin.</b>\n"
            "💡 Saldo poin/coin-mu aman, tidak terpotong.",
        )
        return await _show_next_draft(client, user, note="🚫 Ditolak." + (" +strike" if sub == "nos" else ""))

    if sub == "rep":
        return await _show_next_report(client, user)

    if sub == "rok":
        await db.reports.update_one(
            {"_id": utils.oid(args[1])}, {"$set": {"status": "closed", "by_admin": user["_id"]}}
        )
        await audit.log("report_close", actor=user["_id"], target=args[1])
        return await _show_next_report(client, user, note="✔️ Laporan ditutup.")

    if sub == "rban":
        rep = await db.reports.find_one({"_id": utils.oid(args[1])})
        if not rep:
            return await _show_next_report(client, user, note="ℹ️ Laporan tidak ditemukan.")
        if len(args) < 3 or args[2] != "yes":  # konfirmasi kedua
            kb = InlineKeyboardMarkup([
                [ui.danger("🚫 Yakin, ban pelaku", f"a:rv:rban:{args[1]}:yes")],
                [InlineKeyboardButton("◀️ Batal", callback_data="a:rv:rep")],
            ])
            return await screens.show(
                client, user["_id"], f"Ban pengguna <code>{rep['against']}</code>?", kb,
            )
        await db.users.update_one({"_id": rep["against"]}, {"$set": {"banned": True}})
        await db.reports.update_one(
            {"_id": rep["_id"]}, {"$set": {"status": "actioned", "by_admin": user["_id"]}}
        )
        await audit.log("ban", actor=user["_id"], target=rep["against"], via="report")
        return await _show_next_report(client, user, note="🚫 Pelaku diban & laporan ditutup.")

    # beranda review
    n_rev = await db.drafts.count_documents({"status": "review"})
    n_rep = await db.reports.count_documents({"status": "open"})
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📝 Antrean Konten ({n_rev})", callback_data="a:rv:q")],
        [InlineKeyboardButton(f"🚩 Laporan ({n_rep})", callback_data="a:rv:rep")],
        _BACK,
    ])
    await screens.show(
        client, user["_id"],
        "🕵️ <b>Review & Laporan</b>\nKonten mencurigakan menunggu keputusanmu.",
        kb,
    )


async def _show_next_draft(client, user, note: str = None):
    d = await db.drafts.find_one({"status": "review"}, sort=[("created_at", 1)])
    if not d:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Review", callback_data="a:rv")]])
        text = "✨ Antrean konten kosong. Kerja bagus!"
        if note:
            text = f"{note}\n\n{text}"
        return await screens.show(client, user["_id"], text, kb)

    sender = await core_users.get(d["user_id"]) or {}
    score, _ = audit.risk(sender)
    level, hits = await moderation.check(
        content_mod.body(d["content"]), " ".join(d.get("tags") or [])
    )
    did = str(d["_id"])
    meta = [
        f"🕵️ <b>REVIEW {drafts.KIND_LABEL.get(d['kind'], d['kind'])}</b>",
        f"Pengirim: <code>{d['user_id']}</code> · risiko {audit.risk_badge(score)}",
        f"Kata terdeteksi: {utils.esc(', '.join(hits)) or '-'}",
    ]
    if d.get("tags"):
        meta.append("#️⃣ " + utils.esc(" ".join("#" + t for t in d["tags"])))
    if note:
        meta.insert(0, note + "\n")
    kb = InlineKeyboardMarkup([
        [
            ui.success("✅ Setujui", f"a:rv:ok:{did}"),
            ui.danger("❌ Tolak", f"a:rv:no:{did}"),
        ],
        [ui.danger("❌ Tolak + Strike", f"a:rv:nos:{did}")],
        [InlineKeyboardButton("◀️ Review", callback_data="a:rv")],
    ])
    await screens.show(client, user["_id"], "\n".join(meta), kb, content=d["content"])


async def _report_content(rep: dict) -> dict | None:
    """Konten yang dilaporkan agar admin menilai dengan adil — bukan ban buta.
    confes → isi confes-nya; thread → confes awal (balasan thread tidak disimpan,
    sesuai desain anti-jejak)."""
    kind = rep.get("kind")
    if kind == "confes":
        conf = await db.confessions.find_one({"_id": utils.oid(rep.get("ref"))})
        return (conf or {}).get("content")
    if kind == "thread":
        thread = await db.threads.find_one({"_id": utils.oid(rep.get("ref"))})
        if thread:
            conf = await db.confessions.find_one({"_id": thread.get("conf_id")})
            return (conf or {}).get("content")
    return None


async def _show_next_report(client, user, note: str = None):
    rep = await db.reports.find_one({"status": "open"}, sort=[("at", 1)])
    if not rep:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Review", callback_data="a:rv")]])
        text = "✨ Tidak ada laporan terbuka."
        if note:
            text = f"{note}\n\n{text}"
        return await screens.show(client, user["_id"], text, kb)

    against = await core_users.get(rep["against"]) or {}
    score, signals = audit.risk(against)
    rid = str(rep["_id"])
    content = await _report_content(rep)
    is_media = bool(content and content.get("type") != "text")

    lines = [
        "🚩 <b>LAPORAN</b>",
        f"Jenis: <b>{rep['kind']}</b> · {utils.fmt_dt(rep['at'])}",
        f"Pelapor: <code>{rep.get('by', '?')}</code>",
        f"Terlapor: <code>{rep['against']}</code> · risiko {audit.risk_badge(score)}",
    ]
    lines += [f"   {s_}" for s_ in signals]

    # isi pesan yang dilaporkan — supaya keputusan ban adil, bukan buta
    lines.append("")
    if not content:
        lines.append(ui.muted("📄 Isi tidak tersedia (mungkin sudah dihapus/kedaluwarsa)."))
    elif is_media:
        lines.append(f"📄 Media dilaporkan: <b>{content_mod.label(content)}</b> (terlampir di atas)")
    else:
        body = content_mod.body(content)
        lines.append("📄 Isi yang dilaporkan:")
        lines.append(ui.quote(utils.esc(utils.take(body, 500))) if body else ui.muted("(teks kosong)"))
    if rep.get("kind") == "thread":
        lines.append(ui.muted("Balasan dalam thread tidak disimpan — yang tampil adalah confes awalnya."))
    if note:
        lines.insert(0, note + "\n")

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✔️ Tutup", callback_data=f"a:rv:rok:{rid}"),
            InlineKeyboardButton("🚫 Ban Pelaku", callback_data=f"a:rv:rban:{rid}"),
        ],
        [InlineKeyboardButton("◀️ Review", callback_data="a:rv")],
    ])
    # media → tampilkan langsung (caption = info laporan); teks → layar teks biasa
    if is_media:
        await screens.show(client, user["_id"], "\n".join(lines), kb, content=content)
    else:
        await screens.show(client, user["_id"], "\n".join(lines), kb)
