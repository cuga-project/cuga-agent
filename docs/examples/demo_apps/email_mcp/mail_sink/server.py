#!/usr/bin/env python3
"""
Local SMTP capture sink.

- Listens on localhost:1025 (no auth)
- For each incoming email:
  - Writes raw RFC822 bytes to ./mcp_mail/<UUID>.eml
  - Parses and writes structured JSON to ./mcp_mail/<UUID>.json

Install deps:
  pip install aiosmtpd

Run:
  python mail_sink/server.py

Test send (quick):
  python - <<'PY'
import smtplib
from email.message import EmailMessage
msg = EmailMessage()
msg["From"] = "sender@example.local"
msg["To"] = "recipient@example.local"
msg["Subject"] = "Hello from smtplib"
msg.set_content("Plain text body.")
with smtplib.SMTP("localhost", 1025) as s:
    s.send_message(msg)
print("Sent.")
PY
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
import os

from aiosmtpd.controller import Controller
from aiosmtpd.handlers import AsyncMessage
from email import policy
from email.parser import BytesParser
from email.message import Message
from email.header import decode_header, make_header


STORAGE_DIR = Path().cwd() / "docs/examples/demo_apps/email_mcp/mcp_mail"
STORAGE_DIR.mkdir(parents=True, exist_ok=True)


def _decode_hdr(value: Optional[str]) -> Optional[str]:
    if not value:
        return value
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _headers_list(msg: Message, name: str) -> List[str]:
    vals = msg.get_all(name, [])
    decoded = [_decode_hdr(v) for v in vals]
    # Split comma-separated address lists while preserving simple strings
    out: List[str] = []
    for v in decoded:
        if not v:
            continue
        # naive split on comma; good enough for local test
        parts = [p.strip() for p in v.split(",") if p.strip()]
        out.extend(parts if parts else [v])
    return out


def _best_effort_bodies(msg: Message) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract best-effort text/plain and text/html payloads.
    For nested multiparts, walk all parts and pick first preferred occurrences.
    """
    text: Optional[str] = None
    html: Optional[str] = None

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = (part.get_content_disposition() or "").lower()
            if disp == "attachment":
                continue
            if ctype == "text/plain" and text is None:
                try:
                    text = part.get_content()
                except Exception:
                    try:
                        text = part.get_payload(decode=True).decode(
                            part.get_content_charset() or "utf-8", errors="replace"
                        )
                    except Exception:
                        pass
            elif ctype == "text/html" and html is None:
                try:
                    html = part.get_content()
                except Exception:
                    try:
                        html = part.get_payload(decode=True).decode(
                            part.get_content_charset() or "utf-8", errors="replace"
                        )
                    except Exception:
                        pass
            if text is not None and html is not None:
                break
    else:
        ctype = msg.get_content_type()
        try:
            payload = msg.get_content()
        except Exception:
            try:
                payload = msg.get_payload(decode=True).decode(
                    msg.get_content_charset() or "utf-8", errors="replace"
                )
            except Exception:
                payload = None
        if ctype == "text/plain":
            text = payload
        elif ctype == "text/html":
            html = payload
    return text, html


def _attachment_metadata(msg: Message) -> List[Dict[str, Any]]:
    """
    Return list of attachments metadata (no file writes).
    """
    out: List[Dict[str, Any]] = []
    for part in msg.walk():
        disp = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        if disp == "attachment" or filename:
            # Some inline parts include filename, consider them attachments for tests
            try:
                raw = part.get_payload(decode=True) or b""
                size = len(raw)
            except Exception:
                size = None
            out.append(
                {
                    "filename": filename or "",
                    "content_type": part.get_content_type(),
                    "size_bytes": size if size is not None else 0,
                }
            )
    return out


class SinkHandler(AsyncMessage):
    async def handle_message(self, message: Message) -> None:
        # We receive an email.message.Message from aiosmtpd when AsyncMessage base is used.
        # But it may be parsed with compat policy; reparse to ensure full policy=default.
        raw_bytes = message.as_bytes(policy=policy.default)
        parsed = BytesParser(policy=policy.default).parsebytes(raw_bytes)

        uid = str(uuid.uuid4())
        eml_path = STORAGE_DIR / f"{uid}.eml"
        json_path = STORAGE_DIR / f"{uid}.json"

        eml_path.write_bytes(raw_bytes)

        text_body, html_body = _best_effort_bodies(parsed)
        attachments = _attachment_metadata(parsed)

        record = {
            "id": uid,
            "stored_at": datetime.now(timezone.utc).isoformat(),
            "subject": _decode_hdr(parsed.get("Subject")),
            "from": _decode_hdr(parsed.get("From")),
            "to": _headers_list(parsed, "To"),
            "cc": _headers_list(parsed, "Cc"),
            "bcc": _headers_list(parsed, "Bcc"),  # usually not present post-send
            "date": _decode_hdr(parsed.get("Date")),
            "message_id": parsed.get("Message-ID"),
            "in_reply_to": _decode_hdr(parsed.get("In-Reply-To")),
            "references": _decode_hdr(parsed.get("References")),
            "text": text_body,
            "html": html_body,
            "attachments": attachments,
            "raw_path": str(eml_path),
        }

        json_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        # Keep running; nothing else to do per-message.


async def main_base():
    handler = SinkHandler()
    # Log all environment variables related to ports
    print("[Email Sink] Environment check:")
    print(
        f"[Email Sink]   DYNACONF_SERVER_PORTS__EMAIL_SINK = {os.environ.get('DYNACONF_SERVER_PORTS__EMAIL_SINK', 'NOT SET')}"
    )
    print(f"[Email Sink]   All DYNACONF env vars: {[k for k in os.environ.keys() if 'DYNACONF' in k]}")

    smtp_port = int(os.environ.get("DYNACONF_SERVER_PORTS__EMAIL_SINK", "1025"))
    print(f"[Email Sink] Using port: {smtp_port}")

    controller = Controller(handler, hostname="127.0.0.1", port=smtp_port, ready_timeout=15)
    controller.start()
    print(
        f"[Email Sink] ✓ SMTP sink successfully started and listening on smtp://127.0.0.1:{smtp_port} (Ctrl+C to stop)"
    )
    print("[Email Sink] Ready to receive emails")
    try:
        # Run forever
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        print("[Email Sink] Shutting down...")
        pass
    finally:
        controller.stop()
        print("[Email Sink] Stopped")


def main():
    asyncio.run(main_base())


if __name__ == "__main__":
    main()
