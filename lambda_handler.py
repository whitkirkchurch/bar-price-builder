import json
import os
from urllib.parse import unquote_plus

import boto3

from airtable_supplier_mapping import AirtableSupplierMappingStore
from supplier_email import (
    extract_supplier_confirmation_text,
    get_message_id,
    get_sender_address,
    is_approved_sender,
    parse_approved_sender_domains,
    parse_raw_email,
)
from supplier_updates import format_supplier_update_report, run_supplier_cost_updates


def _get_notification_from() -> str:
    notification_from = os.getenv("NOTIFICATION_FROM")
    if not notification_from:
        msg = "NOTIFICATION_FROM environment variable is not set"
        raise ValueError(msg)
    return notification_from


def _get_approved_sender_domains() -> frozenset[str]:
    raw_domains = os.getenv("APPROVED_SENDER_DOMAINS")
    if raw_domains is None or not raw_domains.strip():
        msg = "APPROVED_SENDER_DOMAINS environment variable is not set"
        raise ValueError(msg)
    return parse_approved_sender_domains(raw_domains)


def _load_raw_email_from_s3(bucket: str, key: str) -> bytes:
    s3_client = boto3.client("s3")
    response = s3_client.get_object(Bucket=bucket, Key=key)
    return response["Body"].read()


def _decode_s3_object_key(key: str) -> str:
    return unquote_plus(key)


def _resolve_s3_location(event: dict) -> tuple[str, str]:
    records = event.get("Records", [])
    if not records:
        msg = "Event does not contain any Records"
        raise ValueError(msg)

    record = records[0]
    if "s3" in record:
        bucket = record["s3"]["bucket"]["name"]
        key = _decode_s3_object_key(record["s3"]["object"]["key"])
        return bucket, key

    ses_record = record.get("ses", {})
    receipt = ses_record.get("receipt", {})
    action = receipt.get("action", {})
    if action.get("type") == "S3":
        return action["bucketName"], _decode_s3_object_key(action["objectKey"])

    mail = ses_record.get("mail", {})
    message_id = mail.get("messageId")
    if message_id:
        bucket = os.getenv("INBOUND_EMAIL_BUCKET")
        if not bucket:
            msg = "INBOUND_EMAIL_BUCKET environment variable is not set"
            raise ValueError(msg)
        return bucket, message_id

    msg = "Could not resolve S3 bucket/key from event"
    raise ValueError(msg)


def _send_reply(
    *,
    ses_client,
    sender: str,
    subject: str,
    body: str,
    in_reply_to: str | None,
) -> None:
    notification_from = _get_notification_from()

    if in_reply_to:
        raw_headers = [
            f"From: {notification_from}",
            f"To: {sender}",
            f"Subject: {subject}",
            f"In-Reply-To: {in_reply_to}",
            f"References: {in_reply_to}",
            "MIME-Version: 1.0",
            "Content-Type: text/plain; charset=UTF-8",
            "",
            body,
        ]
        ses_client.send_raw_email(
            Source=notification_from,
            Destinations=[sender],
            RawMessage={"Data": "\r\n".join(raw_headers).encode("utf-8")},
        )
        return

    ses_client.send_email(
        Source=notification_from,
        Destination={"ToAddresses": [sender]},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
        },
    )


def _failure_body(error: str) -> str:
    return "\n".join(
        [
            "Supplier cost update failed.",
            "",
            "Errors:",
            f"  {error}",
        ],
    )


def handler(event: dict, _context) -> dict:
    ses_client = boto3.client("ses")
    sender: str | None = None
    in_reply_to: str | None = None
    approved_domains: frozenset[str] | None = None

    try:
        bucket, key = _resolve_s3_location(event)
        raw_email = _load_raw_email_from_s3(bucket, key)
        message = parse_raw_email(raw_email)
        sender = get_sender_address(message)
        in_reply_to = get_message_id(message)

        approved_domains = _get_approved_sender_domains()
        if not is_approved_sender(sender, approved_domains):
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "reply_sent": False,
                        "reason": "unapproved_sender",
                        "sender": sender,
                    },
                ),
            }

        confirmation_text = extract_supplier_confirmation_text(message)
        mapping_store = AirtableSupplierMappingStore.from_env()
        report = run_supplier_cost_updates(
            confirmation_text=confirmation_text,
            mapping_store=mapping_store,
            apply=True,
            write_mapping=True,
        )
        body = format_supplier_update_report(report)
        subject = "Supplier cost update results"
    except Exception as exc:  # noqa: BLE001
        body = _failure_body(str(exc))
        subject = "Supplier cost update failed"
        if sender is None or approved_domains is None or not is_approved_sender(sender, approved_domains):
            return {
                "statusCode": 500,
                "body": json.dumps({"error": str(exc), "reply_sent": False}),
            }

    _send_reply(
        ses_client=ses_client,
        sender=sender,
        subject=subject,
        body=body,
        in_reply_to=in_reply_to,
    )

    return {
        "statusCode": 200,
        "body": json.dumps({"reply_sent": True, "recipient": sender}),
    }
