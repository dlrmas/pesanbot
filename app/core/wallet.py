"""Dompet: poin harian (reset lazy 00:00 WIB) + coin tersimpan + VIP.

Prinsip CLAUDE.md §6: saldo hanya terpakai SETELAH pengiriman berhasil, dan
semua perubahan tercatat di ledger dengan kunci `ref` unik (aman dari proses ganda).
Reset harian tanpa scheduler: cukup bandingkan tanggal WIB tersimpan.
"""
from pymongo.errors import DuplicateKeyError

from app import db
from app.core import users as core_users
from app.core import utils

METHOD_VIP, METHOD_POINTS, METHOD_COIN = "vip", "points", "coin"


def _points_state(user: dict, s: dict) -> tuple[int, int]:
    """(terpakai_hari_ini, jatah_hari_ini) — sudah memperhitungkan reset lazy."""
    today = utils.today()
    used = user.get("points_used", 0) if user.get("points_date") == today else 0
    extra = user.get("extra_points", 0) if user.get("extra_date") == today else 0
    return used, s["daily_points"] + extra


def points_left(user: dict, s: dict) -> int:
    used, quota = _points_state(user, s)
    return max(0, quota - used)


def summary(user: dict, s: dict) -> dict:
    used, quota = _points_state(user, s)
    return {
        "points_left": max(0, quota - used),
        "points_quota": quota,
        "coins": user.get("coins", 0),
        "vip": core_users.is_vip(user),
        "vip_until": utils.aware(user.get("vip_until")),
    }


def quote(user: dict, s: dict) -> tuple[str | None, str]:
    """Metode bayar menfes + label biaya untuk preview."""
    if core_users.is_vip(user):
        return METHOD_VIP, "Gratis — 👑 VIP aktif"
    left = points_left(user, s)
    if left > 0:
        return METHOD_POINTS, f"1 poin ⚡ (sisa {left} hari ini)"
    if user.get("coins", 0) > 0:
        return METHOD_COIN, f"1 coin 🪙 (saldo {user['coins']})"
    return None, "Poin harian habis & coin kosong"


async def commit(uid: int, method: str, ref: str, note: str = "") -> bool:
    """Potong saldo setelah pengiriman sukses — idempoten lewat `ref` unik."""
    delta = 0 if method == METHOD_VIP else -1
    try:
        await db.ledger.insert_one({
            "ref": ref, "user_id": uid, "kind": method,
            "delta": delta, "note": note, "at": utils.now(),
        })
    except DuplicateKeyError:
        return True  # sudah pernah diproses
    today = utils.today()
    if method == METHOD_POINTS:
        # reset lazy bila hari berganti, lalu hitung pemakaian
        await db.users.update_one(
            {"_id": uid, "points_date": {"$ne": today}},
            {"$set": {"points_date": today, "points_used": 0}},
        )
        await db.users.update_one({"_id": uid}, {"$inc": {"points_used": 1}})
    elif method == METHOD_COIN:
        r = await db.users.update_one(
            {"_id": uid, "coins": {"$gte": 1}}, {"$inc": {"coins": -1}}
        )
        if r.modified_count == 0:  # anomali: saldo habis di tengah jalan
            # jangan tinggalkan entri −1 yang menyesatkan (saldo tak ikut berubah);
            # pengiriman sudah terlanjur sukses, jadi cukup batalkan catatan & audit.
            await db.ledger.delete_one({"ref": ref})
            await db.audit.insert_one({
                "kind": "wallet_anomaly", "user_id": uid, "ref": ref, "at": utils.now(),
            })
    return True


async def add_coins(uid: int, n: int, reason: str, by: int = None, ref: str = None) -> bool:
    entry = {"user_id": uid, "kind": "coin", "delta": n, "note": reason, "by": by, "at": utils.now()}
    if ref:
        entry["ref"] = ref
    try:
        await db.ledger.insert_one(entry)
    except DuplicateKeyError:
        return False  # reward dengan ref sama sudah pernah diberikan
    await db.users.update_one({"_id": uid}, {"$inc": {"coins": n}})
    return True


async def take_coins(uid: int, n: int, reason: str, by: int = None) -> bool:
    r = await db.users.update_one({"_id": uid, "coins": {"$gte": n}}, {"$inc": {"coins": -n}})
    if r.modified_count == 0:
        return False
    await db.ledger.insert_one({
        "user_id": uid, "kind": "coin", "delta": -n, "note": reason, "by": by, "at": utils.now(),
    })
    return True


async def grant_points(uid: int, n: int, reason: str, by: int = None):
    """Poin tambahan berlaku untuk hari ini saja (sifat poin: tidak terakumulasi)."""
    today = utils.today()
    await db.users.update_one(
        {"_id": uid, "extra_date": {"$ne": today}},
        {"$set": {"extra_date": today, "extra_points": 0}},
    )
    await db.users.update_one({"_id": uid}, {"$inc": {"extra_points": n}})
    await db.ledger.insert_one({
        "user_id": uid, "kind": "points_bonus", "delta": n, "note": reason, "by": by, "at": utils.now(),
    })


async def grant_vip(uid: int, days: int, reason: str, by: int = None):
    user = await core_users.get(uid)
    base = utils.aware(user.get("vip_until")) if user else None
    start = base if base and base > utils.now() else utils.now()
    from datetime import timedelta
    until = start + timedelta(days=days)
    await db.users.update_one({"_id": uid}, {"$set": {"vip_until": until}})
    await db.ledger.insert_one({
        "user_id": uid, "kind": "vip", "delta": days, "note": reason, "by": by, "at": utils.now(),
    })
    return until


async def revoke_vip(uid: int, by: int = None):
    await db.users.update_one({"_id": uid}, {"$set": {"vip_until": None}})
    await db.ledger.insert_one({
        "user_id": uid, "kind": "vip", "delta": 0, "note": "dicabut", "by": by, "at": utils.now(),
    })
