"""Sort people's names the way users expect alphabetical order to work.

The contacts API sorted names with Python's default string order, which
compares Unicode code points. Every capital letter comes before every
lower-case one ("Zoe" before "anna") and accented letters come after "z"
("Émile" after "Zoe"), so the A-Z directory put real customers on the
wrong page and the "jump to E" index never showed "Émile".

The fix sorts by a collation key: names are decomposed (NFKD), accents and
case are removed for the primary comparison, a few letters that do not
decompose (ø, ł, æ...) are mapped by hand, and ties are broken by the
accented and cased form so the order is still total and deterministic.
"""
from __future__ import annotations

import unicodedata

# Letters that are separate characters, not "base letter + accent", so NFKD
# leaves them alone. Mapping them keeps "Øster" next to "Oster".
LETTER_FOLDS = {
    "ø": "o", "ł": "l", "đ": "d", "ð": "d", "þ": "th",
    "æ": "ae", "œ": "oe", "ı": "i",
}
# Bucket for names that do not start with a Latin letter (digits, emoji, CJK).
OTHER_GROUP = "#"


# --- the original, buggy version ---------------------------------------------
def naive_sort_names(names: list[str]) -> list[str]:
    """Original endpoint: code point order."""
    return sorted(names)


def naive_first_letter(name: str) -> str:
    """Original A-Z index: the raw first character, upper-cased."""
    return name[:1].upper()


# --- the fix -----------------------------------------------------------------
def strip_accents(text: str) -> str:
    """Remove combining marks after compatibility decomposition (é -> e)."""
    decomposed = unicodedata.normalize("NFKD", text)
    base_characters = []
    for character in decomposed:
        if not unicodedata.combining(character):
            base_characters.append(character)
    return "".join(base_characters)


def fold_letters(text: str) -> str:
    """Case-fold and map letters that have no accent decomposition."""
    folded = []
    for character in text.casefold():
        folded.append(LETTER_FOLDS.get(character, character))
    return "".join(folded)


def primary_key(name: str) -> str:
    """What a reader compares first: letters only, no accents, no case."""
    return fold_letters(strip_accents(name.strip()))


def collation_key(name: str) -> tuple[str, str, str]:
    """Sort key: base letters first, then accents, then case.

    The later parts only matter when the earlier ones tie, so "Emile",
    "emile" and "Émile" sit together but still in a fixed order.
    """
    stripped = name.strip()
    return (primary_key(stripped), stripped.casefold(), stripped)


def sort_names(names: list[str]) -> list[str]:
    """Fixed endpoint: alphabetical order as people read it."""
    return sorted(names, key=collation_key)


def first_letter(name: str) -> str:
    """A-Z index bucket for a name; non-Latin starts go under '#'."""
    key = primary_key(name)
    if key and "a" <= key[0] <= "z":
        return key[0].upper()
    return OTHER_GROUP


def group_by_first_letter(names: list[str]) -> dict[str, list[str]]:
    """Directory sections in display order, each sorted alphabetically."""
    groups: dict[str, list[str]] = {}
    for name in sort_names(names):
        groups.setdefault(first_letter(name), []).append(name)
    return groups
