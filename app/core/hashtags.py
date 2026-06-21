"""Hashtag: validasi, ikon admin, saran pintar, dan statistik pemakaian."""
import re

from pymongo.errors import DuplicateKeyError

from app import db
from app.core import moderation
from app.core.normalize import normalize

_TAG_RE = re.compile(r"^[a-z0-9_]{2,30}$")


def clean_tag(s: str) -> str | None:
    t = (s or "").strip().lstrip("#").lower().replace("-", "_")
    return t if _TAG_RE.match(t) else None


async def all_tags(active_only: bool = True) -> list[dict]:
    q = {"active": True} if active_only else {}
    return [t async for t in db.hashtags.find(q).sort("uses", -1)]


async def docs_map(tags: list[str]) -> dict:
    docs = [t async for t in db.hashtags.find({"tag": {"$in": tags}})]
    return {d["tag"]: d for d in docs}


def render(tags: list[str], dmap: dict) -> str:
    parts = []
    for t in tags:
        emoji = (dmap.get(t) or {}).get("emoji", "")
        parts.append(f"{emoji}#{t}" if emoji else f"#{t}")
    return " ".join(parts)


async def validate(raw_tags: list[str], s: dict) -> tuple[list[str] | None, str | None]:
    """Bersihkan & periksa hashtag pengguna; (tags, None) atau (None, error)."""
    tags = []
    for r in raw_tags:
        t = clean_tag(r)
        if not t:
            return None, f"Hashtag <code>{r[:20]}</code> tidak valid (2–30 huruf/angka/_)."
        if t not in tags:
            tags.append(t)
    if len(tags) > s["max_hashtags"]:
        return None, f"Maksimal {s['max_hashtags']} hashtag."
    level, _ = await moderation.check(" ".join(tags))
    if level != moderation.CLEAN:
        return None, "Hashtag mengandung kata yang tidak diizinkan."
    return tags, None


async def suggest(text: str, limit: int = 5) -> list[dict]:
    """Saran pintar: cocokkan daftar hashtag admin dengan isi pesan + popularitas."""
    norm = f" {normalize(text)} "
    scored = []
    for t in await all_tags():
        score = t.get("uses", 0)
        if f" {t['tag'].replace('_', ' ')} " in norm or f" {t['tag']} " in norm:
            score += 1000
        scored.append((score, t))
    scored.sort(key=lambda x: -x[0])
    return [t for _, t in scored[:limit]]


async def bump(tags: list[str]):
    if tags:
        await db.hashtags.update_many({"tag": {"$in": tags}}, {"$inc": {"uses": 1}})


# ---- kelola (admin) ----

async def add(tag: str, emoji: str = "") -> bool:
    t = clean_tag(tag)
    if not t:
        return False
    try:
        await db.hashtags.insert_one({"tag": t, "emoji": emoji.strip(), "active": True, "uses": 0})
    except DuplicateKeyError:
        await db.hashtags.update_one({"tag": t}, {"$set": {"emoji": emoji.strip(), "active": True}})
    return True


async def remove(tag: str) -> bool:
    r = await db.hashtags.delete_one({"tag": clean_tag(tag) or ""})
    return r.deleted_count > 0


async def toggle(tag: str) -> bool:
    d = await db.hashtags.find_one({"tag": clean_tag(tag) or ""})
    if not d:
        return False
    await db.hashtags.update_one({"_id": d["_id"]}, {"$set": {"active": not d.get("active", True)}})
    return True
