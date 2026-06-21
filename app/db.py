"""MongoDB (PyMongo Async API): koneksi, koleksi, indeks, dan pengaturan sistem."""
import time
from datetime import datetime, timezone

from pymongo import AsyncMongoClient

client: AsyncMongoClient = None
database = None

# Koleksi (diikat saat init) — akses selalu lewat atribut modul: db.users, db.drafts, ...
users = states = screens = drafts = posts = confessions = targets = threads = None
words = hashtags = vouchers = voucher_claims = ledger = audit = reports = None
locks = settings_col = admins = blocks = broadcasts = None

DEFAULT_SETTINGS = {
    "menfes_enabled": True,
    "confes_enabled": True,
    "paused": False,
    "assistant_enabled": True,
    "channel_id": None,
    "channel_title": None,
    "banner_enabled": False,
    "banner_file_id": None,
    "daily_points": 3,
    "menfes_cooldown": 90,
    "confes_cooldown": 180,
    "reply_cooldown": 30,
    "max_text": 2200,
    "max_caption": 900,
    "max_hashtags": 5,
    "max_pending_per_target": 5,
    "max_pending_per_pair": 2,
    "thread_ttl_h": 24,
    "moods": [
        {"key": "curhat", "emoji": "🫂", "label": "Curhat"},
        {"key": "lucu", "emoji": "😂", "label": "Lucu"},
        {"key": "nanya", "emoji": "🙋", "label": "Nanya"},
        {"key": "romantis", "emoji": "💘", "label": "Romantis"},
        {"key": "random", "emoji": "🎲", "label": "Random"},
        {"key": "serius", "emoji": "🧠", "label": "Serius"},
    ],
}

_cache = {"t": 0.0, "doc": None}


async def init(cfg):
    global client, database
    global users, states, screens, drafts, posts, confessions, targets, threads
    global words, hashtags, vouchers, voucher_claims, ledger, audit, reports
    global locks, settings_col, admins, blocks, broadcasts

    client = AsyncMongoClient(cfg.mongo_uri, tz_aware=True)
    database = client[cfg.db_name]

    users = database["users"]
    states = database["states"]
    screens = database["screens"]
    drafts = database["drafts"]
    posts = database["posts"]
    confessions = database["confessions"]
    targets = database["targets"]
    threads = database["threads"]
    words = database["words"]
    hashtags = database["hashtags"]
    vouchers = database["vouchers"]
    voucher_claims = database["voucher_claims"]
    ledger = database["ledger"]
    audit = database["audit"]
    reports = database["reports"]
    locks = database["locks"]
    settings_col = database["settings"]
    admins = database["admins"]
    blocks = database["blocks"]
    broadcasts = database["broadcasts"]

    await locks.create_index("exp", expireAfterSeconds=0)
    await drafts.create_index("expire_at", expireAfterSeconds=0)
    await drafts.create_index([("user_id", 1), ("status", 1)])
    await ledger.create_index("ref", unique=True, sparse=True)
    await ledger.create_index([("user_id", 1), ("at", -1)])
    await voucher_claims.create_index([("code", 1), ("user_id", 1)], unique=True)
    await blocks.create_index([("target_id", 1), ("sender_id", 1)], unique=True)
    await confessions.create_index([("target_id", 1), ("status", 1)])
    await confessions.create_index([("sender_id", 1), ("created_at", -1)])
    await words.create_index("w", unique=True)
    await hashtags.create_index("tag", unique=True)
    await posts.create_index([("created_at", -1)])
    await audit.create_index([("at", -1)])
    await reports.create_index([("status", 1), ("at", -1)])
    await threads.create_index("expire_at", expireAfterSeconds=0)  # bersih otomatis saat kedaluwarsa
    await threads.create_index("a_id")
    await threads.create_index("b_id")
    await targets.create_index("token", sparse=True)
    await users.create_index("username", sparse=True)

    base = dict(DEFAULT_SETTINGS)
    base["created_at"] = datetime.now(timezone.utc)
    await settings_col.update_one({"_id": "core"}, {"$setOnInsert": base}, upsert=True)


async def get_settings(fresh: bool = False) -> dict:
    """Pengaturan sistem dengan cache singkat (15 dtk)."""
    if not fresh and _cache["doc"] and time.monotonic() - _cache["t"] < 15:
        return _cache["doc"]
    doc = await settings_col.find_one({"_id": "core"}) or dict(DEFAULT_SETTINGS)
    for k, v in DEFAULT_SETTINGS.items():
        doc.setdefault(k, v)
    _cache.update(t=time.monotonic(), doc=doc)
    return doc


async def update_settings(**kv):
    await settings_col.update_one({"_id": "core"}, {"$set": kv}, upsert=True)
    _cache["t"] = 0.0
