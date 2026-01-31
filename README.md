# Second Brain Telegram Bot

A productivity Telegram bot that turns voice messages into structured notes in Google Sheets. Uses OpenAI Whisper for transcription and GPT for categorization and data extraction. Supports search over your data, deletion by intent, daily/weekly summaries, and customizable prompts.

## Features

- **Voice → Sheet**: Send a voice message; the bot transcribes it, picks a category from your Settings, fills the row by sheet headers, and writes to the target sheet + Inbox.
- **Intent from voice**: The bot infers whether you want to **add** a note, **ask** a question over your data, or **delete** a record—no buttons required.
- **Search**: Ask questions in voice; the bot answers using data from all category sheets.
- **Delete**: Say you want to remove something; the bot finds candidates and lets you pick one to delete (from both the category sheet and Inbox).
- **Required fields**: Mark columns with `*` in the header (e.g. `Приоритет*`); if empty, the bot asks for values in one round (e.g. `Поле=значение; Поле=значение` or just the value for a single field).
- **Daily / weekly summaries**: Optional summaries from Inbox, sent to a chat you choose, with configurable time and timezone in `/settings`.
- **Custom prompts**: Edit router and extract prompts via `/settings` (stored in the Prompts sheet).

## Requirements

- Python 3.10+
- Telegram Bot Token ([@BotFather](https://t.me/BotFather))
- OpenAI API key (Whisper + GPT)
- Google Cloud project with Sheets API enabled and a service account (JSON key)

## Google Sheets setup

1. Create a spreadsheet and share it with the service account email (Editor).
2. **Sheets:**
   - **Settings**: Col A = Category (sheet name), Col B = Description (for the router).
   - **Inbox**: Backup of every note (Date, Category, Transcript). Created/used by the bot.
   - **Prompts** (optional): Key / Value for custom prompts. The bot can create it when you save a prompt from `/settings`.
   - One sheet per category (same name as in Settings Col A), first row = column headers. Use `*` for required columns (e.g. `Приоритет*`).

## Local setup

```bash
# Clone
git clone https://github.com/morf3uzzz/second-brain-telegram-bot.git
cd second-brain-telegram-bot

# Env
cp .env.example .env
# Edit .env: TELEGRAM_TOKEN, OPENAI_API_KEY, GOOGLE_SHEET_ID, ALLOWED_USER_IDS, ALLOWED_USERNAMES

# Service account key as service_account.json (or service_account.json.json) in project root

# Venv + run
python3 -m venv .venv
.venv/bin/pip install aiogram openai gspread python-dotenv
.venv/bin/python main.py
```

## Server deploy (e.g. Beget)

```bash
git clone https://github.com/morf3uzzz/second-brain-telegram-bot.git
cd second-brain-telegram-bot
cp .env.example .env
# Add .env and service_account.json
bash setup.sh
```

`setup.sh` installs dependencies, creates a systemd service, and enables it. Logs: `journalctl -u second-brain-bot -f`.

## Environment variables

| Variable           | Description                                      |
|--------------------|--------------------------------------------------|
| TELEGRAM_TOKEN     | Bot token from BotFather                         |
| OPENAI_API_KEY     | OpenAI API key                                   |
| GOOGLE_SHEET_ID    | Spreadsheet ID from the sheet URL                |
| ALLOWED_USER_IDS   | Comma-separated Telegram user IDs                |
| ALLOWED_USERNAMES  | Comma-separated usernames (without @)            |

## Usage

- **Add a note**: Send a voice message; the bot adds it to the right sheet and Inbox.
- **Ask**: Send a voice message that is a question; the bot answers from your sheets.
- **Delete**: Say something like “удали задачу про X”; choose from the list, then the row is removed from the sheet and Inbox.
- **Settings**: Send `/settings` to configure prompts, summary chat, daily/weekly time and timezone.

## License

MIT License. See [LICENSE](LICENSE).
