# Drawer Assistant

Telegram-помощница художницы: Hermes, дедлайны и генерация референсов.
Сейчас подготовлены окружение и правила; прикладные инструменты ещё не реализованы.

## Настройка на Windows

Hermes устанавливает собственный Python. Используем его и отдельную `.venv` проекта.
Нужны [Hermes](https://hermes-agent.nousresearch.com/docs/getting-started/installation/),
[uv](https://docs.astral.sh/uv/getting-started/installation/) и Git.
Для PR нужен [GitHub CLI](https://cli.github.com/) с отдельным входом `gh auth login`.

Из корня репозитория в новом PowerShell:

```powershell
uv sync --locked --no-python-downloads
uv run python -X utf8 -m pre_commit install
hermes profile create drawer-assistant --no-alias --no-skills
# Только при первой настройке, если .env ещё нет:
Copy-Item .env.example .env
```

В `.env` заполни `TELEGRAM_BOT_TOKEN` от BotFather и числовой
`TELEGRAM_ALLOWED_USERS`. `COMFYUI_BASE_URL` укажи, когда будет готов GPU-сервер.
Все поля объяснены в `.env.example`; настоящий `.env` игнорируется Git.

```powershell
uv run --env-file .env -- hermes.cmd --profile drawer-assistant model
uv run --env-file .env -- hermes.cmd --profile drawer-assistant tools
uv run --env-file .env -- hermes.cmd --profile drawer-assistant gateway
```

В `model` выбери вход через ChatGPT / OpenAI Codex; в `tools` настрой генерацию
через OpenAI (Codex auth). OAuth выполняется в интерфейсе Hermes, токены в `.env`
не нужны. Отправь своему боту `/start` для начала диалога.
Профиль создаётся один раз; если он уже есть, пропусти `profile create`.
Суффикс `.cmd` нужен для запуска Windows-лаунчера через uv.
`-X utf8` при установке Git hook нужен для путей с кириллицей в Windows.
На Linux используй `hermes` и замени `${LOCALAPPDATA}` в `.env` на свой каталог.
Доступность генерации проверяется на конкретном аккаунте.
Hermes читает Telegram-переменные; `DRAWER_*` и `COMFYUI_*` пока зарезервированы
для будущего плагина и сами по себе генерацию через ComfyUI не включают.

## Проверки

```powershell
uv run poe check
uv run poe audit
```

`uv.lock` фиксирует зависимости. CI повторяет проверки на Windows и Linux.
`uv run poe test` предназначен для тестов этапа 1; тестов поведения пока нет.
Правила документации и разработки — в [AGENTS.md](AGENTS.md).

## GPU-сервер

Для теста нужен ComfyUI с работающим workflow, его моделями и custom nodes.
Передай workflow в формате **API**, названия/версии моделей и адрес сервера.
С ноутбука должны быть доступны загрузка картинки, `/prompt`, `/history` и `/view`.
Предпочтителен SSH-туннель или закрытая сеть; для HTTPS-прокси предусмотрен
необязательный `COMFYUI_API_KEY` (Bearer). У обычного ComfyUI ключа нет.

Архитектура и этапы — в [ARCHITECTURE.md](ARCHITECTURE.md).
