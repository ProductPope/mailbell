"""Reads new emails over IMAP without marking them as read."""

from __future__ import annotations

import email
import email.policy
import imaplib
import json
import re
from dataclasses import dataclass
from email.header import decode_header, make_header
from html import unescape
from pathlib import Path


@dataclass
class Email:
    uid: int
    sender: str
    to: str
    subject: str
    body: str


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", html)
    text = unescape(re.sub(r"<[^>]+>", " ", html))
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip()


def parse_message(uid: int, raw: bytes) -> Email:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    plain, html = None, None
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        ctype = part.get_content_type()
        try:
            content = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True) or b""
            content = payload.decode("utf-8", "replace")
        if ctype == "text/plain" and plain is None:
            plain = content
        elif ctype == "text/html" and html is None:
            html = content
    body = plain if plain else _html_to_text(html or "")
    return Email(
        uid=uid,
        sender=_decode(msg.get("From")),
        to=_decode(msg.get("To")),
        subject=_decode(msg.get("Subject")),
        body=body or "",
    )


class State:
    """Remembers the last email we looked at, so each email is checked only once."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict = {}
        if path.exists():
            try:
                self.data = json.loads(path.read_text())
            except json.JSONDecodeError:
                self.data = {}

    def get(self, key: str) -> dict | None:
        return self.data.get(key)

    def set(self, key: str, uidvalidity: int, last_uid: int) -> None:
        self.data[key] = {"uidvalidity": uidvalidity, "last_uid": last_uid}
        self.path.write_text(json.dumps(self.data, indent=2))


class Mailbox:
    def __init__(self, host: str, port: int, user: str, password: str, folder: str):
        self.host, self.port, self.user, self.password, self.folder = host, port, user, password, folder
        self.conn: imaplib.IMAP4_SSL | None = None

    @property
    def key(self) -> str:
        return f"{self.user}@{self.host}/{self.folder}"

    def connect(self) -> None:
        self.conn = imaplib.IMAP4_SSL(self.host, self.port)
        self.conn.login(self.user, self.password)
        typ, _ = self.conn.select(f'"{self.folder}"', readonly=True)
        if typ != "OK":
            raise RuntimeError(f"Cannot open folder {self.folder}")

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.logout()
            except Exception:
                pass
            self.conn = None

    def uidvalidity(self) -> int:
        assert self.conn
        typ, data = self.conn.status(f'"{self.folder}"', "(UIDVALIDITY UIDNEXT)")
        m = re.search(rb"UIDVALIDITY (\d+)", data[0])
        return int(m.group(1)) if m else 0

    def max_uid(self) -> int:
        assert self.conn
        typ, data = self.conn.uid("search", None, "ALL")
        uids = [int(u) for u in data[0].split()] if data and data[0] else []
        return max(uids) if uids else 0

    def fetch_after(self, last_uid: int, limit: int = 50) -> list[Email]:
        assert self.conn
        self.conn.noop()  # lets the server tell us about newly arrived mail
        typ, data = self.conn.uid("search", None, f"UID {last_uid + 1}:*")
        uids = sorted(int(u) for u in data[0].split() if int(u) > last_uid) if data and data[0] else []
        emails = []
        for uid in uids[:limit]:
            # BODY.PEEK so the email stays unread in your mail app
            typ, msg_data = self.conn.uid("fetch", str(uid), "(BODY.PEEK[])")
            if typ != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                continue
            emails.append(parse_message(uid, msg_data[0][1]))
        return emails
