"""Unit tests for supplier_email.py."""

from pathlib import Path

import pytest

from supplier_data import parse_supplier_confirmation_rows
from supplier_email import (
    extract_supplier_confirmation_text,
    get_message_id,
    get_sender_address,
    parse_raw_email,
)

HB_CLARK_EMAIL = Path(__file__).parent / "fixtures" / "emails" / "hb_clark_sales_order_ack.eml"
HB_CLARK_FORWARDED_EMAIL = Path(__file__).parent / "fixtures" / "emails" / "hb_clark_sales_order_ack_forwarded.eml"


def test_extract_supplier_confirmation_text_from_hb_clark_email() -> None:
    raw = HB_CLARK_EMAIL.read_bytes()
    message = parse_raw_email(raw)

    text = extract_supplier_confirmation_text(message)
    rows = parse_supplier_confirmation_rows(text)

    assert get_sender_address(message) == "ZOE.LLOYD@hbclark.co.uk"
    assert get_message_id(message) == "<de736877-f4a0-96c6-3baa-b2b2930b3e0c@hbclark.co.uk>"
    assert "Quantity" in text
    assert "EAN code" in text
    assert len(rows) >= 5
    supplier_codes = {row["supplier_code"] for row in rows}
    assert 10045 in supplier_codes
    assert 24878 in supplier_codes
    assert 12188 in supplier_codes


def test_extract_supplier_confirmation_text_from_forwarded_hb_clark_email() -> None:
    raw = HB_CLARK_FORWARDED_EMAIL.read_bytes()
    message = parse_raw_email(raw)

    text = extract_supplier_confirmation_text(message)
    rows = parse_supplier_confirmation_rows(text)

    assert get_sender_address(message) == "nick@whitkirkchurch.org.uk"
    assert get_message_id(message) == "<a38772d2-8013-4066-b8a2-10ff467671c8n@whitkirk.com>"
    assert "Quantity" in text
    assert "EAN code" in text
    assert len(rows) == 13
    supplier_codes = {row["supplier_code"] for row in rows}
    assert 24879 in supplier_codes
    assert 23824 in supplier_codes
    assert 12005 in supplier_codes
    assert 22195 in supplier_codes


def test_get_sender_address_prefers_reply_to() -> None:
    raw = b"""From: supplier@example.com
Reply-To: operator@whitkirkchurch.org.uk
To: supplier-updates@whitkirkchurch.org.uk
Subject: Test
MIME-Version: 1.0
Content-Type: text/plain; charset=UTF-8

Quantity Code Description Size Pack Price EAN code
1 10045 BLACK SHEEP BEST BITTER KEG 11G 3.8%11G 1 140.82 5024583088007
"""
    message = parse_raw_email(raw)
    assert get_sender_address(message) == "operator@whitkirkchurch.org.uk"


def test_extract_supplier_confirmation_text_raises_when_missing() -> None:
    raw = b"""From: operator@whitkirkchurch.org.uk
To: supplier-updates@whitkirkchurch.org.uk
Subject: Empty
MIME-Version: 1.0
Content-Type: text/plain; charset=UTF-8

No supplier table here.
"""
    message = parse_raw_email(raw)
    with pytest.raises(ValueError, match="No supplier confirmation table found"):
        extract_supplier_confirmation_text(message)
