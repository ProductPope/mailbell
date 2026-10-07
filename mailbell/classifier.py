"""Asks the local Nimble decision model (via Ollama /v1/systemone) about an email."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import NimbleConfig, RulesConfig
from .mail import Email


@dataclass
class Verdict:
    important: bool
    important_probability: float
    category: str
    category_probability: float
    reason: str  # short human readable note: "model", "vip sender", "ignored sender"


class NimbleError(Exception):
    pass


def build_request(email: Email, nimble: NimbleConfig, rules: RulesConfig) -> dict:
    body = email.body.strip()
    if len(body) > nimble.max_body_chars:
        body = body[: nimble.max_body_chars] + "\n[...truncated]"
    return {
        "model": nimble.model,
        "state": {
            "from": email.sender,
            "to": email.to,
            "subject": email.subject,
            "body": body,
        },
        "questions": {
            "important": {
                "type": "noul",
                "instructions": rules.important,
                "criteria": {
                    "true": "The email is important and needs attention now.",
                    "false": "The email can wait or be ignored.",
                },
            },
            "category": {
                "type": "choice",
                "instructions": "Which category best describes this email?",
                "criteria": rules.categories,
            },
        },
    }


def _matches(sender: str, patterns: list[str]) -> bool:
    s = sender.lower()
    return any(p.lower() in s for p in patterns if p)


def classify(email: Email, nimble: NimbleConfig, rules: RulesConfig, post=None) -> Verdict:
    """Return a Verdict. `post` can be injected in tests instead of a real HTTP call."""
    if _matches(email.sender, rules.ignore_senders):
        return Verdict(False, 0.0, "ignored", 1.0, "ignored sender")
    if _matches(email.sender, rules.vip_senders):
        return Verdict(True, 1.0, "vip", 1.0, "vip sender")

    payload = build_request(email, nimble, rules)
    response = (post or _post)(nimble.url, payload, nimble.timeout)

    try:
        answers = response["answers"]
        p_important = float(answers["important"]["noul"])
        cat = answers["category"]
        category = cat["choice"]
        p_category = float(cat["probabilities"].get(category, 0.0))
    except (KeyError, TypeError, ValueError) as e:
        raise NimbleError(f"Unexpected answer from Nimble: {response!r}") from e

    return Verdict(
        important=p_important >= rules.threshold,
        important_probability=p_important,
        category=category,
        category_probability=p_category,
        reason="model",
    )


def _post(url: str, payload: dict, timeout: float) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise NimbleError(f"Nimble returned HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise NimbleError(
            f"Cannot reach Ollama at {url}. Is Ollama (0.35 or newer) running and "
            f"did you run 'ollama pull nimble'? ({e.reason})"
        ) from e
