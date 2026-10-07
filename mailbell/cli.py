"""Command line entry point: `mailbell run`, `mailbell try`, `mailbell check`."""

from __future__ import annotations

import argparse
import sys
import time
import tomllib
from datetime import datetime
from pathlib import Path

from . import __version__
from .alert import desktop_notification, play_sound
from .classifier import NimbleError, classify
from .config import ConfigError, NimbleConfig, RulesConfig, load_config
from .mail import Email, Mailbox, State


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def cmd_run(args) -> int:
    cfg = load_config(args.config)
    state = State(cfg.state_file)
    box = Mailbox(cfg.imap.host, cfg.imap.port, cfg.imap.user, cfg.imap.password, cfg.imap.folder)
    log(f"mailbell {__version__}: watching {box.key} every {cfg.poll_seconds}s (Ctrl+C to stop)")

    while True:
        try:
            if box.conn is None:
                box.connect()
                validity = box.uidvalidity()
                saved = state.get(box.key)
                if not saved or saved["uidvalidity"] != validity:
                    # First run: start from now, don't ring for your whole inbox history.
                    state.set(box.key, validity, box.max_uid())
                    log("First run for this mailbox: only emails arriving from now on will be checked.")

            saved = state.get(box.key)
            for mail in box.fetch_after(saved["last_uid"]):
                handle(mail, cfg, args.dry_run)
                state.set(box.key, saved["uidvalidity"], mail.uid)
        except NimbleError as e:
            log(f"Nimble problem: {e}")  # don't advance; the email will be retried
        except (OSError, Exception) as e:  # network drops, IMAP timeouts
            log(f"Mailbox problem, reconnecting: {e}")
            box.close()
        if args.once:
            box.close()
            return 0
        try:
            time.sleep(cfg.poll_seconds)
        except KeyboardInterrupt:
            box.close()
            log("Bye.")
            return 0


def handle(mail: Email, cfg, dry_run: bool) -> None:
    v = classify(mail, cfg.nimble, cfg.rules)
    mark = "!!" if v.important else "  "
    log(f"{mark} {v.important_probability:.2f} [{v.category}] {mail.sender} | {mail.subject}")
    if v.important and not dry_run:
        play_sound(cfg.alert.sound)
        if cfg.alert.desktop_notification:
            desktop_notification(f"Important email: {mail.sender}", mail.subject or "(no subject)")


def _partial_config(path: str) -> tuple[NimbleConfig, RulesConfig]:
    """For `try`: works without mailbox settings, falls back to defaults."""
    p = Path(path)
    raw = tomllib.loads(p.read_text()) if p.exists() else {}
    rules_raw = dict(raw.get("rules") or {})
    if "categories" in rules_raw:
        rules_raw["categories"] = {k: (v or None) for k, v in rules_raw["categories"].items()}
    return NimbleConfig(**(raw.get("nimble") or {})), RulesConfig(**rules_raw)


def cmd_try(args) -> int:
    nimble, rules = _partial_config(args.config)
    body = args.body if args.body is not None else sys.stdin.read()
    mail = Email(uid=0, sender=args.sender, to="me", subject=args.subject, body=body)
    v = classify(mail, nimble, rules)
    print(f"important:  {'YES' if v.important else 'no'} (probability {v.important_probability:.2f}, threshold {rules.threshold})")
    print(f"category:   {v.category} ({v.category_probability:.2f})")
    if v.important and args.sound:
        play_sound()
    return 0


def cmd_check(args) -> int:
    ok = True
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"Config:   FAIL  {e}")
        return 1
    print("Config:   OK")

    try:
        classify(Email(0, "test@example.com", "me", "Hello", "Just checking in."), cfg.nimble, cfg.rules)
        print(f"Nimble:   OK  ({cfg.nimble.url})")
    except NimbleError as e:
        print(f"Nimble:   FAIL  {e}")
        ok = False

    box = Mailbox(cfg.imap.host, cfg.imap.port, cfg.imap.user, cfg.imap.password, cfg.imap.folder)
    try:
        box.connect()
        print(f"Mailbox:  OK  ({box.key})")
    except Exception as e:
        print(f"Mailbox:  FAIL  {e}")
        ok = False
    finally:
        box.close()

    print("Sound:    playing a test sound now...")
    play_sound(cfg.alert.sound)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mailbell", description="Rings a bell when an important email arrives. Runs fully local with Nimble."
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("-c", "--config", default="config.toml", help="path to config file (default: config.toml)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="watch the mailbox and ring on important emails")
    p_run.add_argument("--dry-run", action="store_true", help="classify and print, but don't make a sound")
    p_run.add_argument("--once", action="store_true", help="check once and exit")
    p_run.set_defaults(func=cmd_run)

    p_try = sub.add_parser("try", help="classify a made-up email to tune your rules")
    p_try.add_argument("--sender", default="someone@example.com")
    p_try.add_argument("--subject", default="")
    p_try.add_argument("--body", help="email text (default: read from standard input)")
    p_try.add_argument("--sound", action="store_true", help="play the sound if it's important")
    p_try.set_defaults(func=cmd_try)

    p_check = sub.add_parser("check", help="test config, Nimble, mailbox and sound")
    p_check.set_defaults(func=cmd_check)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, NimbleError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nBye.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
