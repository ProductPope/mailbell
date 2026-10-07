# mailbell 🔔

[![tests](https://github.com/ProductPope/mailbell/actions/workflows/tests.yml/badge.svg)](https://github.com/ProductPope/mailbell/actions/workflows/tests.yml)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Runs 100% locally](https://img.shields.io/badge/AI-100%25%20local-purple)

**Rings a bell when an important email arrives. Everything runs on your own computer.**

mailbell checks your inbox every minute, asks a small AI model running locally on your machine
"is this email important?", and plays a sound (plus a desktop notification) only when the answer is yes.
Your emails never leave your computer: no cloud AI, no API keys, no subscription.

It uses [Nimble](https://ollama.com/library/nimble), an open 9B *decision model* from Bespoke Labs,
running in [Ollama](https://ollama.com). Unlike a chat model, Nimble does not write text: it answers
yes/no and multiple choice questions with a probability, in about a tenth of a second. That makes it
a great fit for sorting email.

```
[10:42:03] !! 0.94 [work] Anna Kowalska <anna@firma.pl> | Contract to sign by Friday
[10:42:03]    0.03 [promotion] Shop <promo@shop.com> | -50% this weekend only
[10:43:04]    0.12 [newsletter] Weekly Digest <news@blog.com> | 5 links you missed
```

## What you need

* A computer with about **10 GB of free memory** (RAM on a Mac with Apple Silicon, or graphics card memory on a PC).
  Nimble is a 9 GB download.
* [Ollama](https://ollama.com/download) **version 0.35 or newer**
* Python 3.11 or newer
* An email account that supports IMAP (Gmail, Outlook, iCloud, WP, Onet, most work mailboxes)

## Setup in 5 steps

**1. Install Ollama and download Nimble**

```bash
ollama pull nimble
```

**2. Install mailbell**

```bash
git clone https://github.com/ProductPope/mailbell.git
cd mailbell
pip install .
```

**3. Create an app password for your mailbox**

For safety, don't use your normal password. Create a separate "app password":

* Gmail: turn on 2-step verification, then go to Google Account > Security > App passwords
* Outlook, iCloud, others: search "<your provider> app password"

Then put it in an environment variable, so it never sits in a file:

```bash
export MAILBELL_PASSWORD="abcd efgh ijkl mnop"     # macOS / Linux
setx MAILBELL_PASSWORD "abcd efgh ijkl mnop"       # Windows (open a new window afterwards)
```

**4. Fill in the config**

```bash
cp config.example.toml config.toml
```

Open `config.toml` and set your email address and IMAP server. The most important setting is
`important` under `[rules]`: describe in plain words what counts as important **for you**.

**5. Check and run**

```bash
mailbell check     # tests Ollama, your mailbox and the sound
mailbell run       # starts watching. Ctrl+C to stop
```

On the first run mailbell only remembers where your inbox currently ends, so it won't ring for old emails.

## Tuning what counts as "important"

Try made-up emails without touching your inbox:

```bash
mailbell try --sender "Mum <mum@gmail.com>" --subject "Dinner on Sunday?" --body "Are you coming?"
mailbell try --sender "noreply@shop.com" --subject "Sale!" --body "Everything -30%"
```

Then adjust in `config.toml`:

| Setting | What it does |
|---|---|
| `important` | Your description of an important email. Be concrete. Write it in English; the emails can be in any language. |
| `threshold` | How sure the model must be (0 to 1). Too many alerts? Raise it. Missing emails? Lower it. |
| `vip_senders` | Always ring for these, no AI involved. |
| `ignore_senders` | Never ring for these. |
| `sound` | Your own sound file. |

Use `mailbell run --dry-run` to watch the decisions for a day without any sound.

## Good to know

* mailbell opens your inbox **read only** and never marks emails as read, moves or deletes anything.
* A probability of 0.9 does not mean the model is right 90% of the time on *your* email.
  Watch it with `--dry-run` first and adjust the threshold.
* Very long emails are shortened before being sent to the model.
* Questions or ideas? Open an issue.

## How it works

1. Every `poll_seconds`, connect to your mailbox over IMAP and look for emails newer than the last one seen.
2. For each new email, send sender, subject and body to `http://localhost:11434/v1/systemone` with two questions:
   *"Is this important?"* (yes/no) and *"Which category?"* (multiple choice).
3. If the "important" probability is above your threshold, play a sound and show a notification.
4. Remember the last email checked in `~/.mailbell_state.json`.

No dependencies beyond the Python standard library.

## Development

```bash
python -m unittest discover -s tests -v
```

## Po polsku

**mailbell** co minutę sprawdza Twoją skrzynkę i pyta lokalny model AI (Nimble w Ollamie), czy nowy mail jest ważny.
Jeśli tak, odtwarza dźwięk i pokazuje powiadomienie. Treść maili nie opuszcza Twojego komputera.

Szybki start:

1. Zainstaluj [Ollamę](https://ollama.com/download) (0.35 lub nowszą) i uruchom `ollama pull nimble`
2. `git clone https://github.com/ProductPope/mailbell.git && cd mailbell && pip install .`
3. Utwórz hasło do aplikacji w swojej poczcie i ustaw je w zmiennej `MAILBELL_PASSWORD`
4. Skopiuj `config.example.toml` do `config.toml` i wpisz adres skrzynki
5. `mailbell check`, a potem `mailbell run`

Polecenie opisujące, co jest ważne, najlepiej pisać po angielsku. Same maile mogą być po polsku.

## License

MIT. Nimble itself is Apache 2.0, by [Bespoke Labs](https://github.com/bespokelabsai/nimble).
