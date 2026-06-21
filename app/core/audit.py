"""Audit & skor risiko — jejak secukupnya untuk admin dan keamanan."""
from app import db
from app.core import utils


async def log(kind: str, /, actor: int = None, target=None, **info):
    """`kind` positional-only: pemanggil bebas memakai kwarg `kind=` di **info."""
    await db.audit.insert_one({
        "kind": kind, "actor": actor, "target": target, "info": info, "at": utils.now(),
    })


async def strike(uid: int, field: str, n: int = 1):
    """field: reports | blocked | strikes."""
    await db.users.update_one({"_id": uid}, {"$inc": {f"risk.{field}": n}})


def risk(user: dict) -> tuple[int, list[str]]:
    """Skor 0–100 + sinyal yang bisa dibaca admin."""
    r = user.get("risk", {})
    signals = []
    score = 0
    if r.get("reports", 0):
        score += min(45, r["reports"] * 15)
        signals.append(f"🚩 {r['reports']}× dilaporkan")
    if r.get("blocked", 0):
        score += min(30, r["blocked"] * 10)
        signals.append(f"⛔ {r['blocked']}× kena kata blocked")
    if r.get("strikes", 0):
        score += min(40, r["strikes"] * 20)
        signals.append(f"⚠️ {r['strikes']} strike moderasi")
    if user.get("warnings", 0):
        score += min(20, user["warnings"] * 10)
        signals.append(f"📣 {user['warnings']} peringatan admin")
    return min(100, score), signals


def risk_badge(score: int) -> str:
    if score >= 70:
        return f"🔴 {score}"
    if score >= 35:
        return f"🟡 {score}"
    return f"🟢 {score}"
