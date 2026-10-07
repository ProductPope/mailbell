# mailbell 🔔

[![tests](https://github.com/ProductPope/mailbell/actions/workflows/tests.yml/badge.svg)](https://github.com/ProductPope/mailbell/actions/workflows/tests.yml)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Runs 100% locally](https://img.shields.io/badge/AI-100%25%20local-purple)
![Status: early prototype](https://img.shields.io/badge/status-early%20prototype-orange)

**Rings a bell when an important email arrives. Everything runs on your own computer.**

mailbell checks your inbox every minute, asks a small AI model running locally on your machine
"is this email important?", and plays a sound (plus a desktop notification) only when the answer is yes.
Your emails never leave your computer: no cloud AI, no API keys, no subscription.

It uses [Nimble](https://ollama.com/library/nimble), an open 9B *decision model* from Bespoke Labs,
running in [Ollama](https://ollama.com). Unlike a chat model, Nimble does not write text: it answers
yes/no and multiple choice questions with a probability, in about a tenth of a second. That makes it
a great fit for sorting email.

> **Status: early prototype.** It works and is tested, but it has had little real-world use yet.
> Try it alongside your normal mail app for a while before you rely on it. See [Limitations](#limitations).

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
* An email account that lets you log in to IMAP with an **app password** (see the table below)

### Which mailboxes work

mailbell currently logs in with a username and an app password. Many providers, and most company
mailboxes, no longer allow that and require a modern sign-in (OAuth2), which mailbell does **not** support yet.

| Mailbox | Works? |
|---|---|
| Personal Gmail | ✅ Yes, with 2-step verification and an app password |
| iCloud Mail, Fastmail, WP, Onet, Interia, most small providers | ✅ Usually, with an app password |
| Google Workspace (company Gmail) | ⚠️ Only if your admin allows app passwords |
| Microsoft 365 / Exchange Online (most company Outlook) | ❌ Not yet (needs OAuth2) |
| Outlook.com / Hotmail | ❌ Not reliably (Microsoft is moving to OAuth2 only) |

OAuth2 support is the top item on the [roadmap](#roadmap).

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
* iCloud, others: search "<your provider> app password"

**4. Fill in the config and store the password**

```bash
cp config.example.toml config.toml
```

Open `config.toml` and set your email address and IMAP server. The most important setting is
`important` under `[rules]`: describe in plain words what counts as important **for you**.

Then store the app password in your system's password vault (macOS Keychain, Windows Credential
Manager or the Linux keyring), so it is never kept as plain text in a file:

```bash
mailbell set-password
```

(Advanced: the `MAILBELL_PASSWORD` environment variable also works and takes priority, but be aware
that on most systems it ends up stored as plain text somewhere, for example in your shell profile.)

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
| `ignore_senders` | Never ring for these, the AI isn't even asked. |
| `sound` | Your own sound file. |

Sender lists match the **real email address only**, never the display name (which anyone can fake):

* `"anna@firma.pl"` matches exactly that address. It does **not** match `joanna@firma.pl`.
* `"@firma.pl"` (or `"firma.pl"`) matches everyone at that domain and its subdomains, but not look-alikes such as `firma.pl.evil.xyz`.

Even so, an email address in the From line can be forged by a determined attacker, so don't treat
"it rang, so it's real" as proof that an email is genuine.

Use `mailbell run --dry-run` to watch the decisions for a day without any sound.

## Good to know

* mailbell opens your inbox **read only** and never marks emails as read, moves or deletes anything.
* A probability of 0.9 does not mean the model is right 90% of the time on *your* email.
  Watch it with `--dry-run` first and adjust the threshold.
* Very long emails are shortened before being sent to the model.
* If several important emails arrive together (for example after your laptop wakes up), you get
  **one** sound and one notification listing them, not a sound per email.
* If an email can't be checked after 3 tries (broken formatting, the model rejects it), mailbell
  skips it so it can't block the rest, logs it, and **rings just in case** so you look at it yourself.
  If Ollama isn't running at all, nothing is skipped: mailbell waits and retries.
* The model is kept in memory for an hour between emails (`keep_alive`), so it doesn't reload 9 GB for every message.
* Folder names with Polish or other non-English letters (e.g. `Ważne`) are supported.
* Questions or ideas? Open an issue.

## Limitations

Please read these before relying on mailbell:

* **It will make mistakes.** On public benchmarks Nimble agrees with human labels about 75% of the
  time, and "is this email important to me?" is more personal than those tests. Use `--dry-run` and
  tune the threshold. There is no "this was wrong" feedback button yet.
* **Email text can try to fool the model.** A spammer can write "URGENT, very important" in an email.
  mailbell tells the model to judge by the real sender and what is actually asked, which helps, but
  can't fully prevent it. The worst outcome is an unnecessary sound, nothing else happens.
* **It needs a lot of memory.** About 10 GB of RAM or graphics memory, all the time while it's loaded.
  On a laptop with 16 GB this is noticeable.
* **No company Outlook / Microsoft 365 yet** (see the table above).
* **It runs in a terminal window.** There is no background service or autostart yet; closing the
  window stops it.
* It checks for new mail every minute rather than being told instantly by the server.

## Roadmap

1. OAuth2 sign-in for Microsoft 365, Outlook.com and Google Workspace
2. Autostart in the background (macOS, Windows, Linux)
3. Marking wrong decisions, to calibrate the threshold on your own email
4. Instant notification from the server (IMAP IDLE) instead of checking every minute

Contributions are welcome.

## How it works

1. Every `poll_seconds`, connect to your mailbox over IMAP and look for emails newer than the last one seen.
2. For each new email, send sender, subject and body to `http://localhost:11434/v1/systemone` with two questions:
   *"Is this important?"* (yes/no) and *"Which category?"* (multiple choice).
3. If the "important" probability is above your threshold, play a sound and show a notification.
4. Remember the last email checked in `~/.mailbell_state.json`.

The only dependency is [`keyring`](https://pypi.org/project/keyring/), used to read your password from the system password vault.

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
3. Utwórz hasło do aplikacji w swojej poczcie
4. Skopiuj `config.example.toml` do `config.toml`, wpisz adres skrzynki i uruchom `mailbell set-password`
   (hasło trafi do systemowego sejfu haseł, a nie do pliku)
5. `mailbell check`, a potem `mailbell run`

Polecenie opisujące, co jest ważne, najlepiej pisać po angielsku. Same maile mogą być po polsku.

Ważne: to wczesna wersja. Firmowe skrzynki Microsoft 365 / Outlook jeszcze nie działają (wymagają logowania OAuth2),
a model będzie się czasem mylił. Szczegóły w sekcji [Limitations](#limitations).

## License

MIT. Nimble itself is Apache 2.0, by [Bespoke Labs](https://github.com/bespokelabsai/nimble).
