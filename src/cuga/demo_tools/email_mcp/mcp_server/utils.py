from __future__ import annotations
import json
import mimetypes
import os
import smtplib
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from email.utils import make_msgid, formatdate
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Dict, List, Optional

MAIL_DIR = Path(__file__).resolve().parents[1] / "mcp_mail"
MAIL_DIR.mkdir(parents=True, exist_ok=True)

# --- Sending ---------------------------------------------------------------


@dataclass
class SendResult:
    success: bool
    message_id: Optional[str]
    error: Optional[str] = None


def _attach_files(msg: EmailMessage, paths: Optional[List[str]]) -> None:
    """
    Attach files (by path) to the outgoing EmailMessage.
    - Guesses MIME type via mimetypes; falls back to 'application/octet-stream'
    - Reads bytes; filename is the basename of the path
    """
    if not paths:
        return
    for p in paths:
        if not p:
            continue
        fp = Path(p).expanduser()
        if not fp.exists() or not fp.is_file():
            raise FileNotFoundError(f"Attachment not found: {p}")
        data = fp.read_bytes()
        ctype, enc = mimetypes.guess_type(fp.name)
        maintype = "application"
        subtype = "octet-stream"
        if ctype:
            maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=fp.name)


def send_email_smtp(
    from_addr: str,
    to_addrs: List[str],
    subject: str,
    text_body: Optional[str] = None,
    html_body: Optional[str] = None,
    cc_addrs: Optional[List[str]] = None,
    bcc_addrs: Optional[List[str]] = None,
    attachments_paths: Optional[List[str]] = None,
    smtp_port: Optional[int] = None,
) -> SendResult:
    """
    Build MIME and send via localhost SMTP sink.
    If both text and html provided, send multipart/alternative.
    Supports file attachments via absolute/relative paths.

    Args:
        smtp_port: SMTP server port. If not provided, reads from DYNACONF_SERVER_PORTS__EMAIL_SINK env var, defaults to 1025.
    """
    if smtp_port is None:
        smtp_port = int(os.environ.get("DYNACONF_SERVER_PORTS__EMAIL_SINK", "1025"))

    print(f"[Email Utils] Preparing to send email via SMTP at 127.0.0.1:{smtp_port}")
    print(f"[Email Utils] From: {from_addr}, To: {to_addrs}, Subject: {subject}")

    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)
    if cc_addrs:
        msg["Cc"] = ", ".join(cc_addrs)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    mid = make_msgid()
    msg["Message-ID"] = mid

    # Body
    if text_body and html_body:
        msg.set_content(text_body)
        msg.add_alternative(html_body, subtype="html")
    elif html_body:
        msg.set_content("This email contains HTML content.")
        msg.add_alternative(html_body, subtype="html")
    else:
        msg.set_content(text_body or "")

    # Attachments
    _attach_files(msg, attachments_paths)

    recipients = list(to_addrs)
    if cc_addrs:
        recipients += cc_addrs
    if bcc_addrs:
        recipients += bcc_addrs

    try:
        print(f"[Email Utils] Connecting to SMTP server at 127.0.0.1:{smtp_port}...")
        with smtplib.SMTP("127.0.0.1", smtp_port) as smtp:
            smtp.send_message(msg, from_addr=from_addr, to_addrs=recipients)
        print(f"[Email Utils] ✓ Email sent successfully, Message-ID: {mid}")
        return SendResult(True, mid)
    except Exception as e:
        print(f"[Email Utils] ✗ Failed to send email: {e}")
        return SendResult(False, None, error=str(e))


# --- Listing / Reading -----------------------------------------------------


def _safe_load(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_emails_fs(query: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """
    Read all ./mcp_mail/*.json, sort by stored_at desc, apply optional free-text filter.

    Query (case-insensitive substring):
    - Searched fields: subject, from, to (array joined into one string), and text (plaintext body).
    - NOT searched: html body, cc, bcc, attachment content (metadata only), Message-ID,
      In-Reply-To, References, other headers.
    - No regex/wildcards: spaces are literal; "weekly report" matches that exact sequence.
    - Unicode: case-insensitive, not accent-insensitive.
    - If query is falsy/omitted, return all.

    Limit:
    - Max number of items returned (>=1). Newest first.
    """
    recs: List[Dict[str, Any]] = []
    for p in sorted(MAIL_DIR.glob("*.json")):
        data = _safe_load(p)
        if not data:
            continue
        recs.append(data)

    def _parse_iso(s: str) -> float:
        try:
            return datetime.fromisoformat(s).timestamp()
        except Exception:
            return 0.0

    recs.sort(key=lambda r: _parse_iso(r.get("stored_at", "")), reverse=True)

    if query:
        q = query.lower()

        def hay(r: Dict[str, Any]) -> str:
            subj = (r.get("subject") or "").lower()
            frm = (r.get("from") or "").lower()
            to = " ".join(r.get("to") or []).lower()
            text = (r.get("text") or "").lower()
            return " | ".join([subj, frm, to, text])

        recs = [r for r in recs if q in hay(r)]

    out = []
    for r in recs[: max(1, limit)]:
        out.append(
            {
                "id": r.get("id"),
                "subject": r.get("subject"),
                "from": r.get("from"),
                "to": r.get("to"),
                "date": r.get("date"),
            }
        )
    return out


# Allowed textual attachment types we will extract & return inline
_ALLOWED_CT = {
    "text/plain",
    "text/csv",
    "application/json",
    "application/yaml",
    "text/yaml",
    "application/x-yaml",
}

_ALLOWED_EXT = {".txt", ".csv", ".json", ".yaml", ".yml"}

# Upper bound to avoid returning huge blobs in JSON
_MAX_TEXT_BYTES = 512 * 1024  # 512 KiB


def _is_allowed_attachment(part_filename: Optional[str], content_type: str) -> bool:
    if content_type in _ALLOWED_CT:
        return True
    if part_filename:
        ext = Path(part_filename).suffix.lower()
        if ext in _ALLOWED_EXT:
            return True
    return False


def _extract_textual_attachments(eml_path: Path) -> List[Dict[str, Any]]:
    """
    Open the .eml and return a list of attachments. For allowed text-y types
    (txt/json/yaml/csv), include `text` with UTF-8 decoded content (up to limit).
    """
    if not eml_path.exists():
        return []
    raw = eml_path.read_bytes()
    msg = BytesParser(policy=policy.default).parsebytes(raw)

    results: List[Dict[str, Any]] = []
    for part in msg.walk():
        disp = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        is_attachment = disp == "attachment" or bool(filename)
        if not is_attachment:
            continue

        ctype = part.get_content_type()
        payload = part.get_payload(decode=True) or b""
        entry: Dict[str, Any] = {
            "filename": filename or "",
            "content_type": ctype,
            "size_bytes": len(payload),
        }

        if _is_allowed_attachment(filename, ctype):
            # Bound size, decode as UTF-8 with replacement
            preview = payload[:_MAX_TEXT_BYTES]
            try:
                entry["text"] = preview.decode(part.get_content_charset() or "utf-8", errors="replace")
            except Exception:
                entry["text"] = preview.decode("utf-8", errors="replace")

        results.append(entry)
    return results


def get_email_fs(email_id: str) -> Dict[str, Any]:
    """
    Return the stored JSON AND (if possible) inline textual attachment contents
    by parsing the associated .eml file. The returned `attachments` field is
    replaced with a richer list that may include `text` for supported types.
    """
    path = MAIL_DIR / f"{email_id}.json"
    if not path.exists():
        return {"error": {"code": "NOT_FOUND", "message": f"Email id {email_id} not found"}}
    data = _safe_load(path)
    if data is None:
        return {"error": {"code": "READ_ERROR", "message": f"Failed to read {path.name}"}}

    eml_path = Path(data.get("raw_path") or "")
    # Merge: prefer freshly parsed attachment info from the .eml
    enriched_attachments = (
        _extract_textual_attachments(eml_path) if eml_path else (data.get("attachments") or [])
    )
    data["attachments"] = enriched_attachments
    return data
