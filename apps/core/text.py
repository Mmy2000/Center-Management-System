"""Text helpers shared by models and services."""

import re
import unicodedata

_ALEF_VARIANTS = "أإآٱ"
_DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]")


def normalize_arabic(text: str) -> str:
    """Fold alef/yaa/taa-marbuta variants and strip diacritics for search.

    "أحمد" and "احمد" are the same person to a receptionist, so they must be the
    same person to the search box. Stored on Student.search_name and applied to
    the query term, so both sides of the comparison are folded.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = _DIACRITICS.sub("", text)
    for char in _ALEF_VARIANTS:
        text = text.replace(char, "ا")
    text = text.replace("ى", "ي").replace("ة", "ه").replace("ـ", "")
    return " ".join(text.split()).strip()
