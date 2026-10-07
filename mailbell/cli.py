"""Command line entry point: `mailbell run`, `try`, `check` and `set-password`."""

from __future__ import annotations

import argparse
import imaplib
import sys
import time
import tomllib
from datetime import datetime
from pathlib import Path

from . import __version__
from .alert import desktop_notification, play_sound
from .classifier import NimbleError, classify
from .config import ConfigError, NimbleConfig, RulesConfig, load_config, store_keychain_password
from .mail import Email, Mailbox, State


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def cmd_run(args) -> int:
    cfg = load_config(args.config)
    state = State(cfg.state_file)
    if state.warning:
        log(f"WARNING: {state.warning}")
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
            check_new_mail(box, state, cfg, args.dry_run)
        except (imaplib.IMAP4.error, OSError) as e:  # network drops, IMAP timeouts, sleep/wake
            log(f"Mailbox problem, reconnecting: {e}")
            box.close()
        if args.once:
            box.close()
            return 0
        time.sleep(cfg.poll_seconds)


def check_new_mail(box: Mailbox, state: State, cfg, dry_run: bool) -> None:
    """Classify every new email once, then ring once for the whole batch."""
    saved = state.get(box.key)
    important: list[Email] = []
    failed: list[int] = []

    for uid in box.new_uids(saved["last_uid"]):
        try:
            mail = box.fetch(uid)
            verdict = classify(mail, cfg.nimble, cfg.rules)
        except (imaplib.IMAP4.error, OSError):
            raise  # connection trouble: not this email's fault, reconnect and retry
        except NimbleError as e:
            if e.transient:
                log(f"Nimble is not reachable, will retry: {e}")
                break  # nothing is lost: we stop here and retry this email next round
            if not _give_up(state, box.key, uid, e, cfg):
                break
            failed.append(uid)
        except Exception as e:  # an email we can't read (broken formatting etc.)
            if not _give_up(state, box.key, uid, e, cfg):
                break
            failed.append(uid)
        else:
            mark = "!!" if verdict.important else "  "
            log(f"{mark} {verdict.important_probability:.2f} [{verdict.category}] {mail.sender} | {mail.subject}")
            if verdict.important:
                important.append(mail)
        state.set(box.key, saved["uidvalidity"], uid)

    if not dry_run:
        ring(important, failed, cfg)


def _give_up(state: State, key: str, uid: int, error: Exception, cfg) -> bool:
    """Count a failed attempt. True means: stop retrying this email and move on."""
    attempts = state.record_failure(key, uid)
    if attempts < cfg.max_attempts:
        log(f"Could not check email #{uid} (attempt {attempts} of {cfg.max_attempts}), will retry: {error}")
        return False
    log(f"SKIPPED email #{uid} after {attempts} failed attempts, please look at it yourself: {error}")
    return True


def ring(important: list[Email], failed: list[int], cfg) -> None:
    """One sound and one notification per round, however many emails arrived."""
    unsure = failed if cfg.alert.on_failure else []
    if not important and not unsure:
        return
    play_sound(cfg.alert.sound)
    if not cfg.alert.desktop_notification:
        return
    if len(important) == 1 and not unsure:
        mail = important[0]
        desktop_notification(f"Important email: {mail.sender}", mail.subject or "(no subject)")
        return
    lines = [f"{m.sender}: {m.subject or '(no subject)'}" for m in important[:3]]
    if len(important) > 3:
        lines.append(f"and {len(important) - 3} more")
    if unsure:
        lines.append(f"{len(unsure)} email(s) could not be checked, have a look yourself")
    title = f"{len(important)} important emails" if important else "mailbell could not check some email"
    desktop_notification(title, "\n".join(lines))


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


def cmd_set_password(args) -> int:
    import getpass

    p = Path(args.config)
    if not p.exists():
        raise ConfigError(f"Config file not found: {p}. Create it first (see README).")
    imap = (tomllib.loads(p.read_text(encoding="utf-8")).get("imap") or {})
    if not imap.get("user") or not imap.get("host"):
        raise ConfigError("Fill in [imap] host and user in your config first.")
    print(f"Storing the app password for {imap['user']} ({imap['host']}) in your system keychain.")
    password = getpass.getpass("App password (typing is hidden): ").strip()
    if not password:
        print("Nothing entered, nothing changed.")
        return 1
    try:
        store_keychain_password(imap["user"], imap["host"], password)
    except Exception as e:
        print(f"Could not use the system keychain ({e}). Use the MAILBELL_PASSWORD environment variable instead.")
        return 1
    print("Saved. You can now run 'mailbell check'.")
    return 0


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

    p_pw = sub.add_parser("set-password", help="store your mailbox app password in the system keychain")
    p_pw.set_defaults(func=cmd_set_password)

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
