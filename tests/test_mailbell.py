import json
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path

from mailbell.classifier import NimbleError, address_matches, build_request, classify, sender_address
from mailbell.config import ConfigError, NimbleConfig, RulesConfig, parse_config
from mailbell.mail import Email, State, imap_utf7, parse_message


def fake_nimble(p_important, category="personal"):
    def post(url, payload, timeout):
        post.payload = payload
        return {
            "model": "nimble",
            "answers": {
                "important": {"type": "noul", "noul": p_important},
                "category": {
                    "type": "choice",
                    "choice": category,
                    "probabilities": {category: 0.9},
                    "confidence": 0.8,
                },
            },
        }

    return post


MAIL = Email(uid=1, sender="Anna <anna@example.com>", to="me@example.com", subject="Umowa", body="Proszę o podpis do piątku.")


class ClassifierTests(unittest.TestCase):
    def test_important_above_threshold(self):
        v = classify(MAIL, NimbleConfig(), RulesConfig(threshold=0.7), post=fake_nimble(0.92))
        self.assertTrue(v.important)
        self.assertEqual(v.category, "personal")

    def test_not_important_below_threshold(self):
        v = classify(MAIL, NimbleConfig(), RulesConfig(threshold=0.7), post=fake_nimble(0.3, "promotion"))
        self.assertFalse(v.important)

    def test_vip_skips_model(self):
        def boom(*a):
            raise AssertionError("model should not be called")

        v = classify(MAIL, NimbleConfig(), RulesConfig(vip_senders=["anna@example.com"]), post=boom)
        self.assertTrue(v.important)
        self.assertEqual(v.reason, "vip sender")

    def test_ignore_wins_over_vip(self):
        v = classify(MAIL, NimbleConfig(), RulesConfig(vip_senders=["anna@example.com"], ignore_senders=["example.com"]))
        self.assertFalse(v.important)

    def test_request_shape_matches_systemone_api(self):
        req = build_request(MAIL, NimbleConfig(), RulesConfig())
        self.assertEqual(req["model"], "nimble")
        self.assertEqual(req["questions"]["important"]["type"], "noul")
        self.assertEqual(req["questions"]["category"]["type"], "choice")
        self.assertIn("subject", req["state"])
        json.dumps(req)  # must be serialisable

    def test_long_body_is_truncated(self):
        long = Email(1, "a@b.c", "me", "x", "a" * 10000)
        req = build_request(long, NimbleConfig(max_body_chars=100), RulesConfig())
        self.assertLess(len(req["state"]["body"]), 200)

    def test_bad_response_raises(self):
        with self.assertRaises(NimbleError):
            classify(MAIL, NimbleConfig(), RulesConfig(), post=lambda *a: {"oops": 1})


class SenderRuleTests(unittest.TestCase):
    """Regression tests for the issues found in the first review."""

    def test_vip_cannot_be_spoofed_with_display_name(self):
        spoof = Email(1, '"szef@autopay.pl" <phish@evil.xyz>', "me", "Pilne", "Zapłać fakturę")
        v = classify(spoof, NimbleConfig(), RulesConfig(vip_senders=["szef@autopay.pl"]), post=fake_nimble(0.1))
        self.assertFalse(v.important)
        self.assertEqual(v.reason, "model")

    def test_vip_cannot_be_spoofed_with_lookalike_domain(self):
        spoof = Email(1, "x@autopay.pl.evil.xyz", "me", "s", "b")
        v = classify(spoof, NimbleConfig(), RulesConfig(vip_senders=["@autopay.pl"]), post=fake_nimble(0.1))
        self.assertFalse(v.important)

    def test_ignore_does_not_silence_similar_names(self):
        joanna = Email(1, "Joanna <joanna@klient.pl>", "me", "s", "b")
        v = classify(joanna, NimbleConfig(), RulesConfig(ignore_senders=["anna@klient.pl"]), post=fake_nimble(0.9))
        self.assertTrue(v.important)

    def test_address_and_domain_matching(self):
        self.assertTrue(address_matches("anna@firma.pl", "Anna@Firma.pl"))
        self.assertTrue(address_matches("anna@firma.pl", "@firma.pl"))
        self.assertTrue(address_matches("anna@firma.pl", "firma.pl"))
        self.assertTrue(address_matches("anna@mail.firma.pl", "firma.pl"))
        self.assertFalse(address_matches("anna@notfirma.pl", "firma.pl"))
        self.assertFalse(address_matches("joanna@firma.pl", "anna@firma.pl"))
        self.assertFalse(address_matches("anna@firma.pl", "anna"))

    def test_sender_address_ignores_display_name(self):
        self.assertEqual(sender_address('"boss@co.com" <phish@evil.xyz>'), "phish@evil.xyz")

    def test_request_warns_model_about_claims_in_text(self):
        req = build_request(MAIL, NimbleConfig(), RulesConfig())
        self.assertIn("not evidence", req["questions"]["important"]["instructions"])
        self.assertEqual(req["state"]["from_address"], "anna@example.com")
        self.assertEqual(req["keep_alive"], "1h")


class MailParsingTests(unittest.TestCase):
    def test_plain_text_and_encoded_subject(self):
        msg = EmailMessage()
        msg["From"] = "Zażółć <z@example.pl>"
        msg["To"] = "me@example.pl"
        msg["Subject"] = "Gęślą jaźń"
        msg.set_content("Treść wiadomości")
        e = parse_message(7, msg.as_bytes())
        self.assertEqual(e.subject, "Gęślą jaźń")
        self.assertIn("Zażółć", e.sender)
        self.assertIn("Treść", e.body)

    def test_html_only_email(self):
        msg = EmailMessage()
        msg["From"] = "a@b.c"
        msg["Subject"] = "hi"
        msg.set_content("<html><style>p{}</style><p>Hello&nbsp;there</p></html>", subtype="html")
        e = parse_message(1, msg.as_bytes())
        self.assertIn("Hello", e.body)
        self.assertNotIn("<p>", e.body)


class ConfigTests(unittest.TestCase):
    def test_missing_password(self):
        with self.assertRaises(ConfigError):
            parse_config({"imap": {"host": "h", "user": "u"}})

    def test_valid_config_and_empty_category_description(self):
        cfg = parse_config(
            {
                "imap": {"host": "h", "user": "u", "password": "p"},
                "rules": {"threshold": 0.8, "categories": {"a": "", "b": "B"}},
            }
        )
        self.assertEqual(cfg.rules.threshold, 0.8)
        self.assertIsNone(cfg.rules.categories["a"])

    def test_example_config_parses(self):
        import tomllib

        raw = tomllib.loads((Path(__file__).parent.parent / "config.example.toml").read_text(encoding="utf-8"))
        raw["imap"]["password"] = "x"
        cfg = parse_config(raw)
        self.assertEqual(cfg.nimble.model, "nimble")


class FolderNameTests(unittest.TestCase):
    def test_polish_folder_names(self):
        self.assertEqual(imap_utf7("INBOX"), "INBOX")
        self.assertEqual(imap_utf7("Ważne"), "Wa&AXw-ne")
        self.assertEqual(imap_utf7("A&B"), "A&-B")
        self.assertEqual(imap_utf7("日本語"), "&ZeVnLIqe-")  # example from RFC 3501


class StateTests(unittest.TestCase):
    def test_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            State(p).set("k", 5, 42)
            self.assertEqual(State(p).get("k")["last_uid"], 42)
            self.assertFalse((Path(d) / "s.json.tmp").exists())

    def test_damaged_state_is_kept_and_reported(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            p.write_text("{broken")
            s = State(p)
            self.assertTrue(s.warning)
            self.assertTrue((Path(d) / "s.json.corrupt").exists())



class FakeBox:
    key = "u@h/INBOX"

    def __init__(self, mails):
        self.mails = mails  # uid -> Email, or an Exception to raise when fetched

    def new_uids(self, last_uid, limit=50):
        return [u for u in sorted(self.mails) if u > last_uid][:limit]

    def fetch(self, uid):
        m = self.mails[uid]
        if isinstance(m, Exception):
            raise m
        return m


class LoopTests(unittest.TestCase):
    def setUp(self):
        from mailbell import cli
        from mailbell.config import AlertConfig, Config, ImapConfig

        self.cli = cli
        self.dir = tempfile.TemporaryDirectory()
        self.state = State(Path(self.dir.name) / "s.json")
        self.state.set(FakeBox.key, 1, 100)
        self.cfg = Config(imap=ImapConfig("h", "u", "p"), alert=AlertConfig())
        self.rings = []
        self._orig = (cli.classify, cli.play_sound, cli.desktop_notification)
        cli.play_sound = lambda path="": self.rings.append("sound")
        cli.desktop_notification = lambda title, msg: self.rings.append(title)
        cli.log = lambda msg: None

    def tearDown(self):
        self.cli.classify, self.cli.play_sound, self.cli.desktop_notification = self._orig
        self.dir.cleanup()

    def mail(self, uid, sender):
        return Email(uid, sender, "me", f"subject {uid}", "body")

    def classify_by_sender(self, mail, nimble, rules):
        from mailbell.classifier import Verdict

        imp = "boss" in mail.sender
        if "poison" in mail.sender:
            raise NimbleError("HTTP 400: bad request")
        return Verdict(imp, 0.9 if imp else 0.1, "work", 0.9, "model")

    def test_poison_email_is_skipped_after_max_attempts_and_rings(self):
        self.cli.classify = self.classify_by_sender
        box = FakeBox({101: self.mail(101, "poison@x.pl"), 102: self.mail(102, "boss@x.pl")})
        for _ in range(self.cfg.max_attempts - 1):
            self.cli.check_new_mail(box, self.state, self.cfg, dry_run=False)
            self.assertEqual(self.state.get(box.key)["last_uid"], 100)  # still retrying
        self.cli.check_new_mail(box, self.state, self.cfg, dry_run=False)
        self.assertEqual(self.state.get(box.key)["last_uid"], 102)  # moved past it
        self.assertEqual(self.rings.count("sound"), 1)  # one ring for the whole batch

    def test_unreadable_email_is_also_skipped(self):
        self.cli.classify = self.classify_by_sender
        box = FakeBox({101: ValueError("broken MIME"), 102: self.mail(102, "a@x.pl")})
        for _ in range(self.cfg.max_attempts):
            self.cli.check_new_mail(box, self.state, self.cfg, dry_run=False)
        self.assertEqual(self.state.get(box.key)["last_uid"], 102)

    def test_ollama_down_never_skips_emails(self):
        def down(*a):
            raise NimbleError("connection refused", transient=True)

        self.cli.classify = down
        box = FakeBox({101: self.mail(101, "boss@x.pl")})
        for _ in range(10):
            self.cli.check_new_mail(box, self.state, self.cfg, dry_run=False)
        self.assertEqual(self.state.get(box.key)["last_uid"], 100)
        self.assertEqual(self.rings, [])

    def test_backlog_after_sleep_rings_once(self):
        self.cli.classify = self.classify_by_sender
        box = FakeBox({100 + i: self.mail(100 + i, "boss@x.pl") for i in range(1, 21)})
        self.cli.check_new_mail(box, self.state, self.cfg, dry_run=False)
        self.assertEqual(self.rings.count("sound"), 1)
        self.assertIn("20 important emails", self.rings)


if __name__ == "__main__":
    unittest.main()
