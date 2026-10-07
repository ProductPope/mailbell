"""Loading and validating the mailbell configuration file."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_IMPORTANT_RULE = (
    "Is this email important enough that the recipient should look at it right now? "
    "Important: a real person writing directly to the recipient and expecting a reply, "
    "deadlines, payments due, security alerts, anything from family or close colleagues. "
    "Not important: newsletters, marketing, promotions, automated notifications, receipts."
)

DEFAULT_CATEGORIES = {
    "personal": "Written by a real person directly to the recipient",
    "work": "Work or business matter that needs attention",
    "finance": "Invoices, payments, bank or tax matters",
    "security": "Login alerts, password resets, account security",
    "newsletter": "Newsletters, blog digests, mailing lists",
    "promotion": "Marketing, sales, discounts, ads",
    "notification": "Automated notifications from apps and services",
    "other": "None of the above",
}


@dataclass
class ImapConfig:
    host: str
    user: str
    password: str
    port: int = 993
    folder: str = "INBOX"


@dataclass
class NimbleConfig:
    url: str = "http://localhost:11434/v1/systemone"
    model: str = "nimble"
    timeout: float = 120.0
    max_body_chars: int = 4000
    # Keep the model in memory between emails so each one isn't a 9 GB cold start.
    # "" = Ollama's default (a few minutes), "-1" = forever, e.g. "2h" = two hours.
    keep_alive: str = "1h"


@dataclass
class RulesConfig:
    important: str = DEFAULT_IMPORTANT_RULE
    threshold: float = 0.7
    categories: dict[str, str | None] = field(default_factory=lambda: dict(DEFAULT_CATEGORIES))
    vip_senders: list[str] = field(default_factory=list)
    ignore_senders: list[str] = field(default_factory=list)


@dataclass
class AlertConfig:
    sound: str = ""  # path to a .wav/.mp3/.aiff file; empty = built-in system sound
    desktop_notification: bool = True
    # Ring "just in case" when an email could not be checked after several tries,
    # so a broken email never silently hides something important.
    on_failure: bool = True


@dataclass
class Config:
    imap: ImapConfig
    nimble: NimbleConfig = field(default_factory=NimbleConfig)
    rules: RulesConfig = field(default_factory=RulesConfig)
    alert: AlertConfig = field(default_factory=AlertConfig)
    poll_seconds: int = 60
    max_attempts: int = 3
    state_file: Path = Path.home() / ".mailbell_state.json"


class ConfigError(Exception):
    pass


KEYCHAIN_SERVICE = "mailbell"


def keychain_account(user: str, host: str) -> str:
    return f"{user}@{host}"


def keychain_password(user: str, host: str) -> str:
    """Read the password from the system keychain (macOS Keychain, Windows
    Credential Manager, Linux Secret Service). Empty string if unavailable."""
    try:
        import keyring

        return keyring.get_password(KEYCHAIN_SERVICE, keychain_account(user, host)) or ""
    except Exception:
        return ""


def store_keychain_password(user: str, host: str, password: str) -> None:
    import keyring

    keyring.set_password(KEYCHAIN_SERVICE, keychain_account(user, host), password)


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"Config file not found: {path}. Copy config.example.toml to config.toml and fill it in."
        )
    with path.open("rb") as f:
        raw = tomllib.load(f)
    return parse_config(raw)


def parse_config(raw: dict) -> Config:
    imap_raw = raw.get("imap") or {}
    for key in ("host", "user"):
        if not imap_raw.get(key):
            raise ConfigError(f"Missing [imap] {key} in config")

    password = (
        os.environ.get("MAILBELL_PASSWORD")
        or keychain_password(imap_raw["user"], imap_raw["host"])
        or imap_raw.get("password", "")
    )
    if not password:
        raise ConfigError(
            "No mailbox password found. Run 'mailbell set-password' to store it safely "
            "in your system keychain (use an app password, not your main password)."
        )

    imap = ImapConfig(
        host=imap_raw["host"],
        user=imap_raw["user"],
        password=password,
        port=int(imap_raw.get("port", 993)),
        folder=imap_raw.get("folder", "INBOX"),
    )

    nimble = NimbleConfig(**(raw.get("nimble") or {}))

    rules_raw = dict(raw.get("rules") or {})
    if "categories" in rules_raw:
        # TOML has no null, so an empty string means "the name describes itself".
        rules_raw["categories"] = {
            k: (v or None) for k, v in rules_raw["categories"].items()
        }
    rules = RulesConfig(**rules_raw)
    if not 0 < rules.threshold < 1:
        raise ConfigError("[rules] threshold must be between 0 and 1")
    if not 2 <= len(rules.categories) <= 26:
        raise ConfigError("[rules] categories must have between 2 and 26 entries")

    alert = AlertConfig(**(raw.get("alert") or {}))

    general = raw.get("general") or {}
    cfg = Config(imap=imap, nimble=nimble, rules=rules, alert=alert)
    cfg.poll_seconds = int(general.get("poll_seconds", cfg.poll_seconds))
    cfg.max_attempts = max(1, int(general.get("max_attempts", cfg.max_attempts)))
    if general.get("state_file"):
        cfg.state_file = Path(general["state_file"]).expanduser()
    return cfg
