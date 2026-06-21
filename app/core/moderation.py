"""Moderasi konten — hasil: clean | suspicious | blocked (CLAUDE.md §12).

Satu mesin untuk menfes, confes, balasan anonim, dan hashtag.
Kata terlarang dicocokkan pada teks ternormalisasi + versi padat (anti-akal-akalan).
"""
import time

from pymongo.errors import DuplicateKeyError

from app import db
from app.core.normalize import normalize, squash

CLEAN, SUSPICIOUS, BLOCKED = "clean", "suspicious", "blocked"

_cache = {"t": 0.0, "words": []}


async def _words() -> list[dict]:
    if time.monotonic() - _cache["t"] > 30:
        _cache["words"] = [w async for w in db.words.find({})]
        _cache["t"] = time.monotonic()
    return _cache["words"]


async def check(*texts: str) -> tuple[str, list[str]]:
    """Periksa gabungan teks; kembalikan (level, daftar kata yang kena)."""
    joined = " ".join(t for t in texts if t)
    if not joined.strip():
        return CLEAN, []
    norm = f" {normalize(joined)} "
    sq = squash(joined)
    hits, level = [], CLEAN
    for w in await _words():
        if f" {w['w']} " in norm or (w["sq"] and w["sq"] in sq):
            hits.append(w["w"])
            if w["sev"] == BLOCKED:
                level = BLOCKED
            elif level != BLOCKED:
                level = SUSPICIOUS
    return level, hits


async def add_word(word: str, sev: str) -> bool:
    w = normalize(word)
    if not w or sev not in (SUSPICIOUS, BLOCKED):
        return False
    try:
        await db.words.insert_one({"w": w, "sq": squash(word), "sev": sev})
    except DuplicateKeyError:
        await db.words.update_one({"w": w}, {"$set": {"sev": sev}})
    _cache["t"] = 0.0
    return True


async def remove_word(word: str) -> bool:
    r = await db.words.delete_one({"w": normalize(word)})
    _cache["t"] = 0.0
    return r.deleted_count > 0


async def list_words(limit: int = 60) -> list[dict]:
    return [w async for w in db.words.find({}).sort("w", 1).limit(limit)]
