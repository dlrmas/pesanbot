"""Konfigurasi runtime dari environment / .env."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    bot_token: str
    assistant_session: str
    owner_ids: frozenset
    mongo_uri: str
    db_name: str


cfg: Config = None


def load() -> Config:
    global cfg
    load_dotenv()
    raw_owners = os.getenv("OWNER_IDS", "").replace(";", ",")
    owners = frozenset(
        int(p.strip()) for p in raw_owners.split(",") if p.strip().lstrip("-").isdigit()
    )
    cfg = Config(
        api_id=int(os.environ["API_ID"]),
        api_hash=os.environ["API_HASH"],
        bot_token=os.environ["BOT_TOKEN"],
        assistant_session=os.getenv("ASSISTANT_SESSION", "").strip(),
        owner_ids=owners,
        mongo_uri=os.getenv("MONGO_URI", "mongodb://localhost:27017"),
        db_name=os.getenv("DB_NAME", "menfes_confes"),
    )
    return cfg
