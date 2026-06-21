"""Admin (Owner): kelola moderator — angkat & cabut peran 'mod'.

Owner berasal dari .env (OWNER_IDS) dan selalu menang; moderator disimpan di
koleksi `admins`. Peran 'mod' inilah yang dicek di router (admin=True)."""
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app import config, db
from app.core import audit, router, screens, ui, utils
from app.core import users as core_users
from app.features.admin.panel import BACK as _BACK


@router.cb("a", "rl", owner=True)
async def _cb_roles(client, cq, args, user):
    sub = args[0] if args else "home"

    if sub == "add":
        await router.set_state(user["_id"], "admin.mod_add")
        return await screens.ask(
            client, user["_id"],
            "🧑‍⚖️ <b>Angkat Moderator</b>\n"
            "Balas dengan ID atau @username pengguna.\n"
            "Moderator bisa: review konten, tangani laporan, ban & peringatkan.",
            placeholder="@username / 123456789",
        )

    if sub == "del" and len(args) > 1:
        uid = utils.parse_int(args[1])
        if len(args) < 3 or args[2] != "yes":  # konfirmasi kedua
            kb = InlineKeyboardMarkup([
                [ui.danger("🗑 Yakin, cabut", f"a:rl:del:{uid}:yes")],
                [ui.btn("◀️ Batal", "a:rl")],
            ])
            return await screens.show(
                client, user["_id"], f"Cabut moderator <code>{uid}</code>?", kb,
            )
        await db.admins.delete_one({"_id": uid})
        await audit.log("mod_remove", actor=user["_id"], target=uid)
        await screens.notify(client, uid, "ℹ️ Peran moderatormu dicabut oleh owner.")
        return await _roles_screen(client, user, note="🗑 Moderator dicabut.")

    await _roles_screen(client, user)


async def _roles_screen(client, user, note: str = None):
    lines = ["🧑‍⚖️ <b>Moderator</b>", ""]
    rows = []
    n = 0
    async for a in db.admins.find({}):
        u = await core_users.get(a["_id"]) or {}
        name = utils.esc(u.get("name", "?"))
        lines.append(f"• {name} <code>{a['_id']}</code> — {a.get('role', 'mod')}")
        rows.append([InlineKeyboardButton(
            f"🗑 {utils.take(u.get('name', '?'), 18)}", callback_data=f"a:rl:del:{a['_id']}",
        )])
        n += 1
    if not n:
        lines.append("<i>Belum ada moderator.</i>")

    owners = ", ".join(f"<code>{o}</code>" for o in config.cfg.owner_ids) or "-"
    lines.append(f"\n👑 Owner (dari .env): {owners}")
    if note:
        lines.insert(0, note + "\n")

    rows.append([InlineKeyboardButton("➕ Angkat Moderator", callback_data="a:rl:add")])
    rows.append(_BACK)
    await screens.show(client, user["_id"], "\n".join(lines), InlineKeyboardMarkup(rows))


@router.state("admin.mod_add")
async def _st_mod_add(client, m, user, st):
    await screens.drop(client, m)
    target = await core_users.resolve_ref(m.text or "")
    if not target:
        return await screens.ask(
            client, user["_id"],
            "🔍 Tidak ketemu di database. Balas ID/@username lain.",
            placeholder="@username / 123456789",
        )
    if target["_id"] in config.cfg.owner_ids:
        await router.clear_state(user["_id"])
        return await _roles_screen(client, user, note="ℹ️ Pengguna itu sudah owner.")

    await router.clear_state(user["_id"])
    await db.admins.update_one(
        {"_id": target["_id"]},
        {"$set": {"role": "mod", "by": user["_id"], "at": utils.now()}},
        upsert=True,
    )
    await audit.log("mod_add", actor=user["_id"], target=target["_id"])
    await screens.notify(
        client, target["_id"],
        "🧑‍⚖️ <b>Kamu diangkat jadi moderator!</b>\nBuka /admin untuk membuka panel.",
        effect="success",
    )
    await _roles_screen(client, user, note=f"✅ {utils.esc(target.get('name'))} kini moderator.")
