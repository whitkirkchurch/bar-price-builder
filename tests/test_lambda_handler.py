"""Tests for lambda_handler.py with mocked AWS services."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import lambda_handler

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "emails"
SUPPLIER_SALES_ORDER_ACK_EMAIL = FIXTURES_DIR / "supplier_sales_order_ack.eml"


@pytest.fixture
def ses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTIFICATION_FROM", "supplier-updates@example.org.uk")
    monkeypatch.setenv("INBOUND_EMAIL_BUCKET", "test-inbound-bucket")
    monkeypatch.setenv("LOYVERSE_PAT", "test-token")
    monkeypatch.setenv("AIRTABLE_PAT", "test-airtable-token")
    monkeypatch.setenv("AIRTABLE_BASE_ID", "appTEST")
    monkeypatch.setenv("AIRTABLE_SUPPLIER_MAPPING_TABLE_ID", "tblTEST")


def _s3_event(key: str = "message-id-123") -> dict:
    return {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": "test-inbound-bucket"},
                    "object": {"key": key},
                },
            },
        ],
    }


def test_resolve_s3_location_decodes_url_encoded_key() -> None:
    bucket, key = lambda_handler._resolve_s3_location(_s3_event("path/with+spaces"))
    assert bucket == "test-inbound-bucket"
    assert key == "path/with spaces"


def test_handler_sends_success_reply(ses_env) -> None:
    raw_email = SUPPLIER_SALES_ORDER_ACK_EMAIL.read_bytes()
    mock_message = MagicMock()
    mock_report = MagicMock()
    mock_report.summary.parsed_rows = 2
    ses_client = MagicMock()

    with (
        patch("lambda_handler._load_raw_email_from_s3", return_value=raw_email),
        patch("lambda_handler.parse_raw_email", return_value=mock_message),
        patch("lambda_handler.extract_supplier_confirmation_text", return_value="confirmation text"),
        patch("lambda_handler.AirtableSupplierMappingStore.from_env", return_value=MagicMock()),
        patch("lambda_handler.run_supplier_cost_updates", return_value=mock_report),
        patch("lambda_handler.boto3.client", return_value=ses_client),
        patch("lambda_handler.get_sender_address", return_value="jane.doe@example.com"),
        patch(
            "lambda_handler.get_message_id",
            return_value="<test-sales-order-ack-001@example.com>",
        ),
        patch("lambda_handler.format_supplier_update_report", return_value="Supplier cost update completed."),
    ):
        response = lambda_handler.handler(_s3_event(), None)

    assert response["statusCode"] == 200
    assert json.loads(response["body"])["reply_sent"] is True
    ses_client.send_raw_email.assert_called_once()
    raw_message = ses_client.send_raw_email.call_args.kwargs["RawMessage"]["Data"].decode("utf-8")
    assert "jane.doe@example.com" in raw_message
    assert "Supplier cost update completed." in raw_message


@patch("lambda_handler.boto3.client")
@patch("lambda_handler._load_raw_email_from_s3")
def test_handler_sends_failure_reply_when_extraction_fails(
    mock_load_email,
    mock_boto_client,
    ses_env,
) -> None:
    raw_email = b"""From: john.doe@example.com
To: supplier-data@bartender.whitkirk.com
Subject: Empty
MIME-Version: 1.0
Content-Type: text/plain; charset=UTF-8

No table here.
"""
    mock_load_email.return_value = raw_email
    ses_client = MagicMock()
    mock_boto_client.return_value = ses_client

    response = lambda_handler.handler(_s3_event(), None)

    assert response["statusCode"] == 200
    ses_client.send_email.assert_called_once()
    message = ses_client.send_email.call_args.kwargs["Message"]["Body"]["Text"]["Data"]
    assert "Supplier cost update failed." in message
    assert "No supplier confirmation table found" in message


@patch("lambda_handler.boto3.client")
@patch("lambda_handler._load_raw_email_from_s3")
def test_handler_does_not_reply_when_sender_missing(
    mock_load_email,
    mock_boto_client,
    ses_env,
) -> None:
    mock_load_email.side_effect = ValueError("Could not resolve email")
    ses_client = MagicMock()
    mock_boto_client.return_value = ses_client

    response = lambda_handler.handler({"Records": []}, None)

    assert response["statusCode"] == 500
    assert json.loads(response["body"])["reply_sent"] is False
    ses_client.send_email.assert_not_called()
    ses_client.send_raw_email.assert_not_called()


def test_handler_reports_loyverse_failure(ses_env) -> None:
    raw_email = SUPPLIER_SALES_ORDER_ACK_EMAIL.read_bytes()
    ses_client = MagicMock()

    with (
        patch("lambda_handler._load_raw_email_from_s3", return_value=raw_email),
        patch("lambda_handler.parse_raw_email", return_value=MagicMock()),
        patch("lambda_handler.extract_supplier_confirmation_text", return_value="confirmation text"),
        patch("lambda_handler.AirtableSupplierMappingStore.from_env", return_value=MagicMock()),
        patch(
            "lambda_handler.run_supplier_cost_updates",
            side_effect=ValueError("LOYVERSE_PAT environment variable is not set"),
        ),
        patch("lambda_handler.boto3.client", return_value=ses_client),
        patch("lambda_handler.get_sender_address", return_value="jane.doe@example.com"),
        patch("lambda_handler.get_message_id", return_value=None),
    ):
        response = lambda_handler.handler(_s3_event(), None)

    assert response["statusCode"] == 200
    ses_client.send_email.assert_called_once()
    message = ses_client.send_email.call_args.kwargs["Message"]["Body"]["Text"]["Data"]
    assert "LOYVERSE_PAT environment variable is not set" in message
