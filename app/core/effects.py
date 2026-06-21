"""Satu logika bersama pemilihan Animated Message Effects (CLAUDE.md §11).

Pakai nama event semantik, bukan ID mentah, agar semua fitur konsisten.
Efek hanya untuk chat privat; jika event tidak cocok, kirim tanpa efek.
"""

# ---- Animated Message Effects ----
EFFECTS = {
    "fire": 5104841245755180586,      # 🔥 Api
    "like": 5107584321108051014,      # 👍 Like
    "dislike": 5104858069142078462,   # 👎 Dislike
    "heart": 5159385139981059251,     # ❤️ Hati
    "party": 5046509860389126442,     # 🎉 Confetti
    "poop": 5046589136895476101,      # 💩 Poop
}

_SEMANTIC = {
    # persetujuan / pengaturan tersimpan
    "saved": "like",
    "approved": "like",
    # reward, referral, hadiah coin
    "reward": "heart",
    "gift": "heart",
    # pengiriman / redeem / VIP berhasil
    "sent": "party",
    "success": "party",
    "vip": "party",
    # event / pengumuman khusus
    "event": "fire",
    # kegagalan ringan non-sensitif (jarang)
    "fail_soft": "dislike",
    # bercanda aman / pengujian admin
    "joke": "poop",
}


def effect_for(event: str | None) -> int | None:
    if not event:
        return None
    key = _SEMANTIC.get(event, event if event in EFFECTS else None)
    return EFFECTS.get(key)
