import json
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path

from mailbell.classifier import NimbleError, build_request, classify
from mailbell.config import ConfigError, NimbleConfig, RulesConfig, parse_config
from mailbell.mail import Email, State, parse_message


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

        v = classify(MAIL, NimbleConfig(), RulesConfig(vip_senders=["anna@"]), post=boom)
        self.assertTrue(v.important)
        self.assertEqual(v.reason, "vip sender")

    def test_ignore_wins_over_vip(self):
        v = classify(MAIL, NimbleConfig(), RulesConfig(vip_senders=["anna"], ignore_senders=["example.com"]))
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


class StateTests(unittest.TestCase):
    def test_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            State(p).set("k", 5, 42)
            self.assertEqual(State(p).get("k"), {"uidvalidity": 5, "last_uid": 42})


if __name__ == "__main__":
    unittest.main()
