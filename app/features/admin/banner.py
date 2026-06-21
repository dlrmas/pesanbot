"""Admin (Owner): banner menu utama — pasang, ganti, pratinjau, aktif/nonaktif, hapus.

Banner disimpan sebagai `file_id` foto di settings (MongoDB). Karena diunggah
admin lewat bot ini, file_id selalu valid untuk dikirim ulang — tanpa file di
repo. Ditampilkan di atas menu utama oleh start.show_main bila `banner_enabled`.
"""
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app import db
from app.core import audit, content as content_mod
from app.core import router, screens, ui
from app.features.admin.panel import BACK as _BACK


@router.cb("a", "bn", owner=True)
async def _cb_banner(client, cq, args, user):
    sub = args[0] if args else "home"

    if sub == "set":
        await router.set_state(user["_id"], "admin.banner_set")
        return await screens.ask(
            client, user["_id"],
            "🖼 <b>Pasang / Ganti Banner</b>\n"
            "Kirim satu <b>foto</b> untuk dijadikan banner menu utama.",
            placeholder="kirim foto…",
        )

    if sub == "toggle":
        s = await db.get_settings(fresh=True)
        if not s.get("banner_file_id"):
            return await cq.answer("Belum ada banner. Pasang dulu.", show_alert=True)
        new = not s.get("banner_enabled")
        await db.update_settings(banner_enabled=new)
        await audit.log("banner_toggle", actor=user["_id"], on=new)
        return await _banner_screen(
            client, user, note="🟢 Banner diaktifkan." if new else "💤 Banner dinonaktifkan."
        )

    if sub == "preview":
        s = await db.get_settings(fresh=True)
        if not s.get("banner_file_id"):
            return await cq.answer("Belum ada banner.", show_alert=True)
        kb = InlineKeyboardMarkup([[ui.btn("◀️ Kembali", "a:bn")]])
        return await screens.show(
            client, user["_id"], "👁 <b>Pratinjau Banner</b>", kb,
            content={"type": "photo", "file_id": s["banner_file_id"]},
        )

    if sub == "del":
        if len(args) < 2 or args[1] != "yes":  # konfirmasi kedua
            kb = InlineKeyboardMarkup([
                [ui.danger("🗑 Yakin, hapus banner", "a:bn:del:yes")],
                [ui.btn("◀️ Batal", "a:bn")],
            ])
            return await screens.show(client, user["_id"], "Hapus banner menu utama?", kb)
        await db.update_settings(banner_file_id=None, banner_enabled=False)
        await audit.log("banner_delete", actor=user["_id"])
        return await _banner_screen(client, user, note="🗑 Banner dihapus.")

    await _banner_screen(client, user)


async def _banner_screen(client, user, note: str = None):
    s = await db.get_settings(fresh=True)
    has = bool(s.get("banner_file_id"))
    on = bool(s.get("banner_enabled"))
    status = ("🟢 aktif" if on else "🔴 nonaktif") if has else "— belum ada"
    lines = [
        ui.title("Banner Menu Utama", "🖼"),
        "",
        ui.field("Status", status),
        ui.muted("Foto yang tampil di atas menu utama saat /start."),
    ]
    if note:
        lines.insert(0, note + "\n")

    rows = []
    if has:
        rows.append([
            InlineKeyboardButton("🖼 Ganti", callback_data="a:bn:set"),
            InlineKeyboardButton("👁 Pratinjau", callback_data="a:bn:preview"),
        ])
        rows.append([
            InlineKeyboardButton("🔴 Nonaktifkan" if on else "🟢 Aktifkan", callback_data="a:bn:toggle"),
            InlineKeyboardButton("🗑 Hapus", callback_data="a:bn:del"),
        ])
    else:
        rows.append([InlineKeyboardButton("➕ Pasang Banner", callback_data="a:bn:set")])
    rows.append(_BACK)
    await screens.show(client, user["_id"], "\n".join(lines), InlineKeyboardMarkup(rows))


@router.state("admin.banner_set")
async def _st_banner_set(client, m, user, st):
    await screens.drop(client, m)
    c = content_mod.extract(m)
    if not c or c["type"] != "photo":
        return await screens.ask(
            client, user["_id"],
            "⚠️ Itu bukan foto. Kirim satu <b>foto</b> untuk banner.",
            placeholder="kirim foto…",
        )
    await router.clear_state(user["_id"])
    await db.update_settings(banner_file_id=c["file_id"], banner_enabled=True)
    await audit.log("banner_set", actor=user["_id"])
    await _banner_screen(client, user, note="✅ Banner tersimpan & diaktifkan.")
