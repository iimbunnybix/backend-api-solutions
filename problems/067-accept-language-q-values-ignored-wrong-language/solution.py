"""Pick the response language from Accept-Language using quality values.

The storefront chose a language by taking the first tag in the
Accept-Language header, or by checking ``"fr" in header``. Browsers list
languages with quality values (``q=``): ``fr;q=0.1, de`` means "German
preferred, French only as a last resort", and ``q=0`` means "never". Users
got French pages, and Swiss German users (``de-CH``) fell back to English.

The fix parses every entry with its q-value (RFC 9110 section 12.5.4), drops
``q=0`` and malformed entries, sorts by q while keeping header order for
ties, and matches each tag exactly first and then by its primary language
(``de-CH`` -> ``de``) before using the site default.
"""
from __future__ import annotations

from dataclasses import dataclass

SUPPORTED_LANGUAGES = ("en", "de", "fr", "pt-BR")
DEFAULT_LANGUAGE = "en"
DEFAULT_QUALITY = 1.0
MIN_QUALITY = 0.0
MAX_QUALITY = 1.0
# RFC 9110 allows at most three decimal places in a q-value; a longer value
# is a sign of a broken or hostile client, so the entry is ignored.
MAX_Q_DECIMALS = 3
WILDCARD = "*"


@dataclass(frozen=True)
class LanguagePreference:
    tag: str          # lower-cased, e.g. "de-ch"
    quality: float    # 0.0 .. 1.0
    position: int     # order in the header, used to break ties


# --- the original, buggy version ---------------------------------------------
def naive_pick_language(header: str) -> str:
    """Original code: first listed tag wins, q-values are ignored."""
    first_entry = header.split(",")[0]
    tag = first_entry.split(";")[0].strip()
    if tag in SUPPORTED_LANGUAGES:
        return tag
    return DEFAULT_LANGUAGE


# --- the fix -------------------------------------------------------------------
def parse_quality(parameter: str) -> float | None:
    """Return the q-value from a "q=0.8" parameter, or None if it is invalid."""
    name, _, value = parameter.partition("=")
    if name.strip().lower() != "q":
        return None
    value = value.strip()
    whole, _, decimals = value.partition(".")
    if not whole.isdigit() or (decimals and not decimals.isdigit()):
        return None
    if len(decimals) > MAX_Q_DECIMALS:
        return None
    quality = float(value)
    if not MIN_QUALITY <= quality <= MAX_QUALITY:
        return None
    return quality


def parse_accept_language(header: str) -> list[LanguagePreference]:
    """Parse the header into preferences; malformed entries are skipped."""
    preferences = []
    for position, entry in enumerate(header.split(",")):
        parts = entry.split(";")
        tag = parts[0].strip().lower()
        if not tag:
            continue
        quality: float | None = DEFAULT_QUALITY
        # Only the q parameter matters; anything else makes the entry unusable.
        for parameter in parts[1:]:
            quality = parse_quality(parameter)
            if quality is None:
                break
        if quality is None:
            continue
        preferences.append(LanguagePreference(tag, quality, position))
    return preferences


def ranked_tags(header: str) -> list[str]:
    """Acceptable tags, best first; q=0 means "not acceptable" and is dropped."""
    acceptable = []
    for preference in parse_accept_language(header):
        if preference.quality > MIN_QUALITY:
            acceptable.append(preference)
    # Python's sort is stable, so equal q-values keep the order the user chose.
    acceptable.sort(key=lambda preference: -preference.quality)
    return [preference.tag for preference in acceptable]


def match_supported(tag: str, supported: tuple[str, ...]) -> str | None:
    """Exact match first (case-insensitive), then the primary language ("de-ch" -> "de")."""
    for language in supported:
        if language.lower() == tag:
            return language
    primary = tag.split("-")[0]
    for language in supported:
        if language.lower() == primary:
            return language
    return None


def excluded_tags(header: str) -> set[str]:
    """Tags the client explicitly refused with q=0."""
    refused = set()
    for preference in parse_accept_language(header):
        if preference.quality == MIN_QUALITY:
            refused.add(preference.tag)
    return refused


def pick_language(header: str | None, supported: tuple[str, ...] = SUPPORTED_LANGUAGES,
                  default: str = DEFAULT_LANGUAGE) -> str:
    """The best supported language for this header, or ``default``."""
    if not header:
        return default
    refused = excluded_tags(header)
    for tag in ranked_tags(header):
        if tag == WILDCARD:
            # "*" accepts anything: the default, unless it was refused.
            if default.lower() not in refused:
                return default
            continue
        language = match_supported(tag, supported)
        if language is not None and language.lower() not in refused:
            return language
    return default
