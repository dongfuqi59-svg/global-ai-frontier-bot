from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from difflib import SequenceMatcher
from html.parser import HTMLParser

CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
WHITESPACE = re.compile(r"\s+")
TITLE_PUNCTUATION = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def sanitize_untrusted_text(value: str | None, max_length: int) -> str:
    if not value:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(value)
        text = " ".join(parser.parts)
    except Exception:
        text = value
    text = CONTROL_CHARS.sub("", html.unescape(text))
    return WHITESPACE.sub(" ", text).strip()[:max_length]


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalized_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return TITLE_PUNCTUATION.sub("", value)


def title_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalized_title(left), normalized_title(right)).ratio()


def content_similarity(left: str, right: str) -> float:
    clean_left = sanitize_untrusted_text(left, 6000).casefold()
    clean_right = sanitize_untrusted_text(right, 6000).casefold()
    if not clean_left or not clean_right:
        return 0.0
    return SequenceMatcher(None, clean_left[:2000], clean_right[:2000]).ratio()
