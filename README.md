# Second Brain Telegram Bot

---

## Русский

Телеграм-бот для продуктивности: голосовые сообщения превращаются в структурированные записи в Google Таблицах. Использует OpenAI Whisper для транскрибации и GPT для категоризации и извлечения данных. Поддерживает поиск по базе, удаление по намерению, ежедневные и еженедельные сводки и настраиваемые промпты.

### Возможности

- **Голос → таблица**: отправьте голосовое — бот расшифрует, выберет категорию из Settings, заполнит строку по заголовкам листа и запишет в целевой лист и Inbox.
- **Намерение из голоса**: для коротких сообщений бот сам понимает, хотите ли вы **добавить** запись, **задать вопрос** по данным или **удалить** запись — кнопки не нужны.
- **Thinking‑mode для длинных голосовых**: если сообщение длиннее 2 минут, бот структурирует мысли и **сохраняет их в «Прочее»** (если листа нет — в Inbox).
- **Поиск**: задайте вопрос голосом — бот ответит на основе всех листов категорий.
- **Удаление**: скажите, что нужно удалить — бот найдёт кандидатов, вы выберете запись; удаление идёт из листа категории и из Inbox.
- **Обязательные поля**: в заголовке колонки поставьте `*` (например `Приоритет*`); если значение пустое, бот спросит уточнение одним сообщением (формат: `Поле=значение; Поле=значение` или просто значение для одного поля).
- **Ежедневные и еженедельные сводки**: краткие сводки из Inbox в выбранный чат, время и таймзона настраиваются в `/settings`.
- **Свои промпты**: редактирование промптов роутера и извлечения через `/settings` (хранятся в листе Prompts).

### Требования

- Python 3.10+
- Токен бота Telegram ([@BotFather](https://t.me/BotFather))
- API-ключ OpenAI (Whisper и GPT)
- Проект в Google Cloud с включённым Sheets API и сервисным аккаунтом (JSON-ключ)

### Настройка Google Таблицы

1. Создайте таблицу и дайте доступ на редактирование email сервисного аккаунта из JSON-ключа.
2. **Листы:**
   - **Settings**: колонка A — категория (название листа), колонка B — описание для роутера.
   - **Inbox**: копия каждой заметки (Дата, Категория, Текст). Создаётся/используется ботом.
   - **Прочее**: свалка для длинных голосовых из Thinking‑mode (рекомендуется).
   - **Prompts** (необязательно): ключ / значение для своих промптов. Создаётся при сохранении промпта из `/settings`.
   - По одному листу на категорию (название как в Settings, колонка A). Первая строка — заголовки. Для обязательных колонок добавьте `*` (например `Приоритет*`).

### Локальный запуск

```bash
# Клонирование
git clone https://github.com/morf3uzzz/second-brain-telegram-bot.git
cd second-brain-telegram-bot

# Переменные окружения
cp .env.example .env
# Заполните .env: TELEGRAM_TOKEN, OPENAI_API_KEY, GOOGLE_SHEET_ID, ALLOWED_USER_IDS, ALLOWED_USERNAMES

# Ключ сервисного аккаунта положите как service_account.json (или service_account.json.json) в корень проекта

# Виртуальное окружение и запуск
python3 -m venv .venv
.venv/bin/pip install aiogram openai gspread python-dotenv
.venv/bin/python main.py
```

### Деплой на сервер (например Beget)

```bash
git clone https://github.com/morf3uzzz/second-brain-telegram-bot.git
cd second-brain-telegram-bot
cp .env.example .env
# Добавьте .env и service_account.json
bash setup.sh
```

Скрипт `setup.sh` ставит зависимости, создаёт systemd-сервис и включает его. Логи: `journalctl -u second-brain-bot -f`.

### Переменные окружения

| Переменная          | Описание                                           |
|---------------------|----------------------------------------------------|
| TELEGRAM_TOKEN      | Токен бота от BotFather                            |
| OPENAI_API_KEY      | API-ключ OpenAI                                    |
| GOOGLE_SHEET_ID     | ID таблицы из URL                                  |
| ALLOWED_USER_IDS    | ID пользователей Telegram через запятую            |
| ALLOWED_USERNAMES   | Юзернеймы без @ через запятую                      |

### Использование

- **Добавить запись**: отправьте голосовое — бот добавит в нужный лист и Inbox.
- **Длинное голосовое (>2 мин)**: бот сначала структурирует мысли и сохраняет в «Прочее» (или в Inbox, если листа нет).
- **Вопрос**: отправьте голосовое с вопросом — бот ответит по вашим таблицам.
- **Удалить**: скажите, например, «удали задачу про X» — выберите из списка; строка удалится из листа и Inbox.
- **Настройки**: команда `/settings` — промпты, чат для сводок, время и таймзона ежедневных/еженедельных сводок.

### Лицензия

MIT. См. [LICENSE](LICENSE).

---

## English

A productivity Telegram bot that turns voice messages into structured notes in Google Sheets. Uses OpenAI Whisper for transcription and GPT for categorization and data extraction. Supports search over your data, deletion by intent, daily/weekly summaries, and customizable prompts.

### Features

- **Voice → Sheet**: Send a voice message; the bot transcribes it, picks a category from your Settings, fills the row by sheet headers, and writes to the target sheet + Inbox.
- **Intent from voice**: For short messages, the bot infers whether you want to **add**, **ask**, or **delete**—no buttons required.
- **Thinking mode for long voice**: If a message is longer than 2 minutes, the bot structures your thoughts and saves to **Прочее** (or Inbox if the sheet is missing).
- **Search**: Ask questions in voice; the bot answers using data from all category sheets.
- **Delete**: Say you want to remove something; the bot finds candidates and lets you pick one to delete (from both the category sheet and Inbox).
- **Required fields**: Mark columns with `*` in the header (e.g. `Приоритет*`); if empty, the bot asks for values in one round (e.g. `Поле=значение; Поле=значение` or just the value for a single field).
- **Daily / weekly summaries**: Optional summaries from Inbox, sent to a chat you choose, with configurable time and timezone in `/settings`.
- **Custom prompts**: Edit router and extract prompts via `/settings` (stored in the Prompts sheet).

### Requirements

- Python 3.10+
- Telegram Bot Token ([@BotFather](https://t.me/BotFather))
- OpenAI API key (Whisper + GPT)
- Google Cloud project with Sheets API enabled and a service account (JSON key)

### Google Sheets setup

1. Create a spreadsheet and share it with the service account email (Editor).
2. **Sheets:**
   - **Settings**: Col A = Category (sheet name), Col B = Description (for the router).
   - **Inbox**: Backup of every note (Date, Category, Transcript). Created/used by the bot.
   - **Прочее**: Catch‑all for long voice messages from Thinking mode (recommended).
   - **Prompts** (optional): Key / Value for custom prompts. The bot can create it when you save a prompt from `/settings`.
   - One sheet per category (same name as in Settings Col A), first row = column headers. Use `*` for required columns (e.g. `Приоритет*`).

### Local setup

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

### Server deploy (e.g. Beget)

```bash
git clone https://github.com/morf3uzzz/second-brain-telegram-bot.git
cd second-brain-telegram-bot
cp .env.example .env
# Add .env and service_account.json
bash setup.sh
```

`setup.sh` installs dependencies, creates a systemd service, and enables it. Logs: `journalctl -u second-brain-bot -f`.

### Environment variables

| Variable           | Description                                      |
|--------------------|--------------------------------------------------|
| TELEGRAM_TOKEN     | Bot token from BotFather                         |
| OPENAI_API_KEY     | OpenAI API key                                   |
| GOOGLE_SHEET_ID    | Spreadsheet ID from the sheet URL                |
| ALLOWED_USER_IDS   | Comma-separated Telegram user IDs                |
| ALLOWED_USERNAMES  | Comma-separated usernames (without @)            |

### Usage

- **Add a note**: Send a voice message; the bot adds it to the right sheet and Inbox.
- **Long voice (>2 min)**: The bot structures the thoughts and saves to Прочее (or Inbox if missing).
- **Ask**: Send a voice message that is a question; the bot answers from your sheets.
- **Delete**: Say something like “удали задачу про X”; choose from the list, then the row is removed from the sheet and Inbox.
- **Settings**: Send `/settings` to configure prompts, summary chat, daily/weekly time and timezone.

### License

MIT License. See [LICENSE](LICENSE).
