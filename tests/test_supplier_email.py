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

SUPPLIER_SALES_ORDER_ACK_EMAIL = Path(__file__).parent / "fixtures" / "emails" / "supplier_sales_order_ack.eml"
SUPPLIER_SALES_ORDER_ACK_FORWARDED_EMAIL = (
    Path(__file__).parent / "fixtures" / "emails" / "supplier_sales_order_ack_forwarded.eml"
)


def test_extract_supplier_confirmation_text_from_supplier_email() -> None:
    raw = SUPPLIER_SALES_ORDER_ACK_EMAIL.read_bytes()
    message = parse_raw_email(raw)

    text = extract_supplier_confirmation_text(message)
    rows = parse_supplier_confirmation_rows(text)

    assert get_sender_address(message) == "jane.doe@example.com"
    assert get_message_id(message) == "<test-sales-order-ack-001@example.com>"
    assert "Quantity" in text
    assert "EAN code" in text
    assert len(rows) >= 5
    supplier_codes = {row["supplier_code"] for row in rows}
    assert 10001 in supplier_codes
    assert 10002 in supplier_codes
    assert 10003 in supplier_codes


def test_extract_supplier_confirmation_text_from_forwarded_supplier_email() -> None:
    raw = SUPPLIER_SALES_ORDER_ACK_FORWARDED_EMAIL.read_bytes()
    message = parse_raw_email(raw)

    text = extract_supplier_confirmation_text(message)
    rows = parse_supplier_confirmation_rows(text)

    assert get_sender_address(message) == "john.doe@example.com"
    assert get_message_id(message) == "<test-forwarded-sales-order-001@example.com>"
    assert "Quantity" in text
    assert "EAN code" in text
    assert len(rows) == 13
    supplier_codes = {row["supplier_code"] for row in rows}
    assert 20001 in supplier_codes
    assert 20002 in supplier_codes
    assert 20003 in supplier_codes
    assert 20014 in supplier_codes


def test_get_sender_address_prefers_reply_to() -> None:
    raw = b"""From: supplier@example.com
Reply-To: jane.doe@example.com
To: supplier-data@bartender.whitkirk.com
Subject: Test
MIME-Version: 1.0
Content-Type: text/plain; charset=UTF-8

Quantity Code Description Size Pack Price EAN code
1 10001 TEST ALE KEG 11G 3.8% 11G 1 99.99 5000000000001
"""
    message = parse_raw_email(raw)
    assert get_sender_address(message) == "jane.doe@example.com"


def test_extract_supplier_confirmation_text_raises_when_missing() -> None:
    raw = b"""From: john.doe@example.com
To: supplier-data@bartender.whitkirk.com
Subject: Empty
MIME-Version: 1.0
Content-Type: text/plain; charset=UTF-8

No supplier table here.
"""
    message = parse_raw_email(raw)
    with pytest.raises(ValueError, match="No supplier confirmation table found"):
        extract_supplier_confirmation_text(message)
