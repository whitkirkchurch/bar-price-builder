import re
from email import message_from_bytes
from email.message import Message
from email.utils import parseaddr
from html import unescape

from supplier_data import contains_supplier_confirmation_header, parse_supplier_confirmation_rows

_HTML_BREAK_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def parse_raw_email(raw_bytes: bytes) -> Message:
    return message_from_bytes(raw_bytes)


def get_sender_address(message: Message) -> str:
    reply_to = message.get("Reply-To")
    if reply_to:
        _name, address = parseaddr(reply_to)
        if address:
            return address

    from_header = message.get("From")
    if not from_header:
        msg = "Email message is missing From and Reply-To headers"
        raise ValueError(msg)

    _name, address = parseaddr(from_header)
    if not address:
        msg = f"Could not parse sender address from From header: {from_header!r}"
        raise ValueError(msg)

    return address


def get_message_id(message: Message) -> str | None:
    message_id = message.get("Message-ID")
    if message_id is None:
        return None
    normalized = message_id.strip()
    return normalized or None


def _normalize_email_body_text(text: str) -> str:
    normalized = unescape(text)
    normalized = _HTML_BREAK_RE.sub("\n", normalized)
    normalized = _HTML_TAG_RE.sub("", normalized)
    return normalized.replace("\r\n", "\n")


def _contains_confirmation_header(text: str) -> bool:
    return contains_supplier_confirmation_header(_normalize_email_body_text(text))


def _confirmation_row_count(text: str) -> int:
    try:
        return len(parse_supplier_confirmation_rows(_normalize_email_body_text(text)))
    except ValueError:
        return 0


def _decode_payload(part: Message) -> str | None:
    payload = part.get_payload(decode=True)
    if payload is None or not isinstance(payload, bytes):
        return None
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _iter_message_parts(message: Message):
    if message.is_multipart():
        yield from message.walk()
        return
    yield message


def _candidate_texts_from_part(part: Message) -> list[str]:
    content_type = part.get_content_type()
    candidates: list[str] = []

    if content_type in {"text/plain", "text/html"}:
        decoded = _decode_payload(part)
        if decoded is not None:
            candidates.append(decoded)

    if content_type == "message/rfc822":
        nested = part.get_payload()
        if isinstance(nested, list):
            for nested_message in nested:
                if isinstance(nested_message, Message):
                    candidates.extend(_collect_confirmation_candidates(nested_message))
        elif isinstance(nested, Message):
            candidates.extend(_collect_confirmation_candidates(nested))

    filename = part.get_filename() or ""
    if filename.lower().endswith(".txt"):
        decoded = _decode_payload(part)
        if decoded is not None:
            candidates.append(decoded)

    return candidates


def _collect_confirmation_candidates(message: Message) -> list[str]:
    candidates: list[str] = []
    for part in _iter_message_parts(message):
        candidates.extend(_candidate_texts_from_part(part))
    return candidates


def extract_supplier_confirmation_text(message: Message) -> str:
    matching_candidates = [
        candidate for candidate in _collect_confirmation_candidates(message) if _contains_confirmation_header(candidate)
    ]

    if not matching_candidates:
        msg = "No supplier confirmation table found in email body or attachments"
        raise ValueError(msg)

    best_candidate = max(matching_candidates, key=_confirmation_row_count)
    return _normalize_email_body_text(best_candidate)
