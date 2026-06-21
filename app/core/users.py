"""Repositori pengguna: ensure, peran admin, peringkat, dan lencana."""
import re

from pymongo import ReturnDocument

from app import config, db
from app.core import utils


async def ensure(tg) -> dict:
    """Upsert pengguna dari objek Telegram; selalu dipanggil di pintu masuk."""
    doc = await db.users.find_one_and_update(
        {"_id": tg.id},
        {
            "$set": {
                "username": tg.username,
                "name": tg.first_name or "Anon",
                "last_seen": utils.now(),
            },
            "$setOnInsert": {
                "joined_at": utils.now(),
                "coins": 0,
                "points_used": 0,
                "points_date": utils.today(),
                "extra_points": 0,
                "extra_date": "",
                "banned": False,
                "warnings": 0,
                "vip_until": None,
                "referred_by": None,
                "referrals": 0,
                "stats": {"menfes": 0, "confes": 0, "replies": 0},
                "risk": {"reports": 0, "blocked": 0, "strikes": 0},
                "custom_cooldown": None,
                "cooldown_until": None,
                "nocd_until": None,
                "inbox": {"enabled": True, "paused_until": None, "text_only": False},
            },
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc


async def get(uid: int) -> dict | None:
    return await db.users.find_one({"_id": uid})


async def get_role(uid: int) -> str | None:
    """'owner' | 'mod' | None. Owner dari env selalu menang."""
    if uid in config.cfg.owner_ids:
        return "owner"
    doc = await db.admins.find_one({"_id": uid})
    return doc["role"] if doc else None


def is_vip(user: dict) -> bool:
    vu = utils.aware(user.get("vip_until"))
    return bool(vu and vu > utils.now())


def rank(user: dict) -> str:
    if is_vip(user):
        return "👑 VIP"
    sent = user.get("stats", {}).get("menfes", 0) + user.get("stats", {}).get("confes", 0)
    strikes = user.get("risk", {}).get("strikes", 0)
    if sent >= 30 and strikes == 0:
        return "🛡️ Tepercaya"
    if sent >= 5:
        return "⚡ Aktif"
    return "🌱 Pengguna Baru"


def badges(user: dict) -> list[str]:
    out = []
    st, rk = user.get("stats", {}), user.get("risk", {})
    total = st.get("menfes", 0) + st.get("confes", 0)
    if total >= 5 and rk.get("strikes", 0) == 0 and rk.get("blocked", 0) == 0:
        out.append("🧼 Pengguna Bersih")
    if total >= 10:
        out.append("🚀 Pengirim Aktif")
    if user.get("referrals", 0) >= 5:
        out.append("🎯 Referral Hunter")
    joined = utils.aware(user.get("joined_at"))
    if joined and (utils.now() - joined).days >= 90:
        out.append("🏛️ Anggota Awal")
    return out


async def resolve_ref(text: str) -> dict | None:
    """Cari pengguna dari input bebas: ID numerik atau @username (dari DB)."""
    t = (text or "").strip().lstrip("@")
    n = utils.parse_int(t)
    if n:
        return await db.users.find_one({"_id": n})
    if not t:
        return None
    return await db.users.find_one(
        {"username": {"$regex": f"^{re.escape(t)}$", "$options": "i"}}
    )
