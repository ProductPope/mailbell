"""Reads new emails over IMAP without marking them as read."""

from __future__ import annotations

import email
import email.policy
import base64
import imaplib
import json
import os
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
    """Remembers the last email we looked at, so each email is checked only once.

    Also counts failed attempts per email, so one broken email can't block the rest.
    """

    def __init__(self, path: Path):
        self.path = path
        self.data: dict = {}
        self.warning = ""
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                broken = path.with_suffix(path.suffix + ".corrupt")
                os.replace(path, broken)
                self.warning = (
                    f"State file was damaged and was moved to {broken}. "
                    "Starting fresh: emails that arrived while mailbell was off will not be checked."
                )

    def get(self, key: str) -> dict | None:
        return self.data.get(key)

    def set(self, key: str, uidvalidity: int, last_uid: int) -> None:
        self.data[key] = {"uidvalidity": uidvalidity, "last_uid": last_uid, "failures": {}}
        self._save()

    def record_failure(self, key: str, uid: int) -> int:
        """Add one failed attempt for this email and return how many there have been."""
        failures = self.data[key].setdefault("failures", {})
        failures[str(uid)] = failures.get(str(uid), 0) + 1
        self._save()
        return failures[str(uid)]

    def _save(self) -> None:
        # Write to a temporary file first, then swap it in one step,
        # so a crash or power cut can never leave a half-written file.
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)


def imap_utf7(folder: str) -> str:
    """Encode a folder name for IMAP ("modified UTF-7", RFC 3501), e.g. 'Ważne' -> 'Wa&AXw-ne'."""
    out, buf = [], []

    def flush():
        if buf:
            b64 = base64.b64encode("".join(buf).encode("utf-16-be")).decode("ascii")
            out.append("&" + b64.rstrip("=").replace("/", ",") + "-")
            buf.clear()

    for ch in folder:
        if 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            buf.append(ch)
    flush()
    return "".join(out)


class Mailbox:
    def __init__(self, host: str, port: int, user: str, password: str, folder: str):
        self.host, self.port, self.user, self.password, self.folder = host, port, user, password, folder
        self.conn: imaplib.IMAP4_SSL | None = None

    @property
    def key(self) -> str:
        return f"{self.user}@{self.host}/{self.folder}"

    @property
    def _mailbox_name(self) -> str:
        return '"' + imap_utf7(self.folder).replace("\\", "\\\\").replace('"', '\\"') + '"'

    def connect(self) -> None:
        self.conn = imaplib.IMAP4_SSL(self.host, self.port)
        self.conn.login(self.user, self.password)
        typ, _ = self.conn.select(self._mailbox_name, readonly=True)
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
        typ, data = self.conn.status(self._mailbox_name, "(UIDVALIDITY UIDNEXT)")
        m = re.search(rb"UIDVALIDITY (\d+)", data[0])
        return int(m.group(1)) if m else 0

    def max_uid(self) -> int:
        assert self.conn
        typ, data = self.conn.uid("search", None, "ALL")
        uids = [int(u) for u in data[0].split()] if data and data[0] else []
        return max(uids) if uids else 0

    def new_uids(self, last_uid: int, limit: int = 50) -> list[int]:
        """Numbers of emails that arrived after last_uid, oldest first."""
        assert self.conn
        self.conn.noop()  # lets the server tell us about newly arrived mail
        typ, data = self.conn.uid("search", None, f"UID {last_uid + 1}:*")
        uids = sorted(int(u) for u in data[0].split() if int(u) > last_uid) if data and data[0] else []
        return uids[:limit]

    def fetch(self, uid: int) -> Email:
        assert self.conn
        # BODY.PEEK so the email stays unread in your mail app
        typ, msg_data = self.conn.uid("fetch", str(uid), "(BODY.PEEK[])")
        if typ != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
            raise ValueError(f"Server returned no content for email {uid}")
        return parse_message(uid, msg_data[0][1])
