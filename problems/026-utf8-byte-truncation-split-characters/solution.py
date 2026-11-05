"""Cut text to a byte limit without breaking characters or emoji.

The push-notification gateway accepts at most ``MAX_PREVIEW_BYTES`` bytes of
UTF-8 for the message preview. The original code cut the *encoded bytes* at
the limit and decoded them. When the cut fell inside a multi-byte character
(Hindi, Chinese, emoji) the decode raised ``UnicodeDecodeError`` and the
notification was never sent; with ``errors="ignore"`` the text silently lost
letters or turned a family emoji into a single person.

The fix moves the cut back to a character boundary, and then further back
until it no longer separates a base character from the marks, joiners and
modifiers that belong to it.
"""
from __future__ import annotations

import unicodedata

MAX_PREVIEW_BYTES = 40

# UTF-8 continuation bytes look like 0b10xxxxxx. A cut before one of them is
# in the middle of a character.
CONTINUATION_MASK = 0b1100_0000
CONTINUATION_PATTERN = 0b1000_0000

ZERO_WIDTH_JOINER = "‍"
VARIATION_SELECTORS = range(0xFE00, 0xFE10)
EMOJI_SKIN_TONES = range(0x1F3FB, 0x1F400)
COMBINING_CATEGORIES = {"Mn", "Mc", "Me"}


def naive_truncate(text: str, max_bytes: int) -> str:
    """The original code: slice the bytes and decode (raises on a split char)."""
    return text.encode("utf-8")[:max_bytes].decode("utf-8")


def is_attached_to_previous(char: str) -> bool:
    """True for characters that only make sense glued to the one before.

    Covers combining marks (accents, Devanagari vowel signs), variation
    selectors, emoji skin tones and the zero-width joiner.
    """
    code_point = ord(char)
    if char == ZERO_WIDTH_JOINER:
        return True
    if code_point in VARIATION_SELECTORS or code_point in EMOJI_SKIN_TONES:
        return True
    return unicodedata.category(char) in COMBINING_CATEGORIES


def byte_cut_to_char_boundary(encoded: bytes, max_bytes: int) -> int:
    """Largest byte position <= max_bytes that starts a whole character."""
    cut = max_bytes
    while cut > 0 and (encoded[cut] & CONTINUATION_MASK) == CONTINUATION_PATTERN:
        cut -= 1
    return cut


def splits_a_cluster(text: str, index: int) -> bool:
    """Would cutting ``text`` before ``index`` separate parts of one symbol?"""
    if index <= 0 or index >= len(text):
        return False
    if is_attached_to_previous(text[index]):
        return True
    # "man + ZWJ + girl" is one emoji; cutting after the ZWJ leaves a man.
    return text[index - 1] == ZERO_WIDTH_JOINER


def truncate_utf8(text: str, max_bytes: int) -> str:
    """Return the longest prefix of whole symbols that fits in ``max_bytes``."""
    if max_bytes < 0:
        raise ValueError("max_bytes must be >= 0")
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    byte_cut = byte_cut_to_char_boundary(encoded, max_bytes)
    char_cut = len(encoded[:byte_cut].decode("utf-8"))
    # Step back over marks and joiners so no symbol is left half-drawn.
    while splits_a_cluster(text, char_cut):
        char_cut -= 1
    return text[:char_cut]


def build_preview(sender: str, message: str) -> str:
    """Notification preview "<sender>: <message>" within the gateway limit."""
    return truncate_utf8(f"{sender}: {message}", MAX_PREVIEW_BYTES)
