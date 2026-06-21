"""Rate limit & idempotensi: lock anti-dobel, cooldown per fitur per pengguna."""
from datetime import timedelta

from pymongo.errors import DuplicateKeyError

from app import db
from app.core import utils


async def lock(key: str, ttl: int = 20) -> bool:
    """Kunci sekali-proses (anti klik ganda / proses paralel)."""
    now = utils.now()
    try:
        await db.locks.insert_one({"_id": key, "exp": now + timedelta(seconds=ttl)})
        return True
    except DuplicateKeyError:
        doc = await db.locks.find_one({"_id": key})
        if doc and utils.aware(doc["exp"]) < now:
            await db.locks.replace_one(
                {"_id": key}, {"_id": key, "exp": now + timedelta(seconds=ttl)}
            )
            return True
        return False


async def unlock(key: str):
    await db.locks.delete_one({"_id": key})


def cooldown_left(user: dict, kind: str, s: dict) -> int:
    """Sisa detik cooldown utk kind: menfes|confes|reply. 0 = boleh jalan."""
    now = utils.now()
    frozen = utils.aware(user.get("cooldown_until"))
    if frozen and frozen > now:  # pembekuan admin menang atas apa pun (hukuman)
        return int((frozen - now).total_seconds())
    nocd = utils.aware(user.get("nocd_until"))
    if nocd and nocd > now:  # voucher bebas cooldown
        return 0
    base = user.get("custom_cooldown") or s.get(f"{kind}_cooldown", 60)
    last = utils.aware(user.get(f"last_{kind}_at"))
    if not last:
        return 0
    left = base - (now - last).total_seconds()
    return max(0, int(left))


async def mark(uid: int, kind: str):
    await db.users.update_one({"_id": uid}, {"$set": {f"last_{kind}_at": utils.now()}})
