"""Utilities for keeping derived text valid UTF-8 without changing originals."""
from __future__ import annotations


REPLACEMENT_CHARACTER = "\ufffd"


def sanitize_unicode_text(value: str | None) -> tuple[str | None, int]:
    """Replace isolated UTF-16 surrogate code points in derived text.

    PDF font maps and a few document parsers can expose an isolated surrogate
    instead of a Unicode scalar value. Python keeps that value in ``str`` but
    SQLite/UTF-8 JSON cannot encode it. The original source bytes remain under
    their content hash; this function only makes a derived text projection
    safe to persist and countably reports what was replaced.
    """
    if value is None:
        return None, 0
    replacements = 0
    changed = False
    cleaned: list[str] = []
    index = 0
    while index < len(value):
        codepoint = ord(value[index])
        # Preserve a valid UTF-16 pair if a parser exposed one as two Python
        # code units; only isolated surrogates are invalid UTF-8 text.
        if 0xD800 <= codepoint <= 0xDBFF and index + 1 < len(value):
            low = ord(value[index + 1])
            if 0xDC00 <= low <= 0xDFFF:
                cleaned.append(chr(0x10000 + ((codepoint - 0xD800) << 10) + (low - 0xDC00)))
                changed = True
                index += 2
                continue
        if 0xD800 <= codepoint <= 0xDFFF:
            cleaned.append(REPLACEMENT_CHARACTER)
            replacements += 1
            changed = True
        else:
            cleaned.append(value[index])
        index += 1
    return ("".join(cleaned) if changed else value), replacements
