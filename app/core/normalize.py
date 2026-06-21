"""Normalisasi teks untuk moderasi — mempersulit pengguna mengakali filter."""
import re
import unicodedata

_LEET = str.maketrans({
    "0": "o", "1": "i", "2": "z", "3": "e", "4": "a", "5": "s",
    "6": "g", "7": "t", "8": "b", "9": "g",
    "@": "a", "$": "s", "!": "i", "+": "t", "€": "e", "£": "l",
})
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")
_REPEATS = re.compile(r"(.)\1+")


def normalize(text: str) -> str:
    """Huruf kecil, buang diakritik, petakan leet-speak, sisakan alfanumerik."""
    t = (text or "").lower()
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.translate(_LEET)
    t = _NON_ALNUM.sub(" ", t)
    return _SPACES.sub(" ", t).strip()


def squash(text: str) -> str:
    """Versi padat: tanpa spasi + huruf berulang diringkas ('aanjjiing' → 'anjing')."""
    t = normalize(text).replace(" ", "")
    return _REPEATS.sub(r"\1", t)
