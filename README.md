# Drawer Assistant

Telegram-помощница художницы на Hermes. Сейчас работают настройка профиля и
ограничение одной группой. Дедлайны и адаптер ComfyUI — следующий этап.

## На этом ноутбуке

Токен, группа и SSH уже записаны в локальный `.env`; ChatGPT подключён к профилю.
Команды из папки `drawer-assistant`, в новом PowerShell:

```powershell
uv run drawer-setup       # применить .env после изменения токена/группы
uv run poe bot-status    # проверить, запущен ли бот
uv run poe bot           # запустить, только если ещё не работает
```

**Один gateway обслуживает все профили.** Команда `poe bot` запускает
`hermes.cmd --profile default gateway run`, а профиль `drawer-assistant` содержит
свой токен, OAuth и ограничения. Не добавляй `--env-file .env` к запуску gateway:
это передаст токен также основному профилю. `--force` и `standalone` не нужны.

Бот отвечает только в заданной группе: упомяни `@имя_бота` или ответь на его
сообщение. Для команд используй `/start@имя_бота`. Личка и другие группы
игнорируются, даже для владелицы. Все участники разрешённой группы могут общаться
с ботом. Privacy mode в BotFather можно оставить включённым.

При запуске в своём терминале оставь окно открытым; Ctrl+C останавливает gateway.
Спящий/выключенный ноутбук не обслуживает бота. Для автозапуска при входе в Windows:
`hermes.cmd --profile default gateway install --start-on-login --start-now`.
Остановка: `hermes.cmd --profile default gateway stop`.

## Первая установка на другой Windows

Установи [Hermes](https://hermes-agent.nousresearch.com/docs/getting-started/installation/),
[uv](https://docs.astral.sh/uv/getting-started/installation/) и Git.
Используем Python из Hermes; второй Python не устанавливаем.

```powershell
uv sync --locked --no-python-downloads
uv run python -X utf8 -m pre_commit install
hermes.cmd profile create drawer-assistant --no-alias --no-skills
Copy-Item .env.example .env  # только если .env ещё нет
hermes.cmd --profile drawer-assistant model
```

В `model` выбери OpenAI Codex / ChatGPT и заверши вход в браузере.
В `.env` нужны `TELEGRAM_BOT_TOKEN` от BotFather и `DRAWER_TELEGRAM_CHAT_ID`.
Для ID добавь бота в группу, отправь `/start@имя_бота`, затем прочитай
`message.chat.id` через Telegram Bot API `getUpdates` при остановленном gateway.
Это отрицательное число; ссылка-приглашение ID не заменяет. Не открывай доступ
всем ради определения ID. У ранее авторизованного бота ID также показывает `/status`.

```powershell
uv run drawer-setup
hermes.cmd --profile drawer-assistant config set image_gen.provider openai-codex
uv run poe bot
```

Настройщик сохраняет исходный `config.before-drawer.yaml`, заменяет настройки
Telegram и сохраняет модель/OAuth. Пустой ID группы вызывает ошибку до записи.
Конфиги профиля лежат в `%LOCALAPPDATA%\hermes\profiles\drawer-assistant`.
После изменения `.env` снова выполни `uv run drawer-setup`; работающий gateway
перечитает профиль в течение 30 секунд. Не редактируй копии токена вручную.

`-X utf8` при установке Git hook нужен для кириллицы в пути Windows.
Для PR нужен вход в [GitHub CLI](https://cli.github.com/) через `gh auth login`
либо создание PR в браузере; SSH-ключ сам по себе CLI не авторизует.

## GPU-сервер и ComfyUI

ComfyUI установлен в `/home/ubuntu/ComfyUI` на предоставленном сервере.
Он слушает только серверный `127.0.0.1:8188`. В отдельном PowerShell:

```powershell
uv run poe comfy-tunnel
```

Оставь окно открытым и открой [ComfyUI](http://127.0.0.1:8188).
Если порт уже занят работающим туннелем, второй запуск не нужен.
Поля `COMFYUI_SSH_*` в `.env` задают ключ, хост и порт. При замене сервера
Thundercompute они могут измениться. `NO_PROXY` исключает localhost из прокси.
Если браузер использует отдельный прокси, добавь localhost в его исключения.

После перезапуска GPU-сервера запусти ComfyUI через SSH:

```bash
cd ~/ComfyUI
.venv/bin/python main.py --listen 127.0.0.1 --port 8188 --disable-auto-launch
```

При настройке 06.10.2026 проверены L40, вычисление CUDA и HTTP-цепочка
`/prompt` → `/history` → `/view`, а также `/upload/image` через туннель.
Проверка HTTP использовала одноцветную картинку без модели. Веса и художественный
workflow ещё не установлены; генерация через бота пока не подключена.
Версии: ComfyUI `7ddf9a4f8aef66bca2eda1be2b936965be12b3b0`, Python 3.12.13,
PyTorch 2.14.1+cu130. Снимок зависимостей: `~/comfyui-installed.txt` на сервере;
лог текущего запуска: `~/comfyui.log`.

Облачный image provider настроен на `openai-codex`; OAuth и текстовый ответ
проверены. Генерацию и правку через этот аккаунт ещё нужно проверить отдельно.
`DRAWER_*` и `COMFYUI_*` не включают будущий плагин сами по себе.

## Разработка

```powershell
uv run poe check
uv run poe audit
```

Проверки включают тесты безопасной настройки, Ruff, типизацию и документацию;
GitHub Actions повторяет их на Windows/Linux. Правила — [AGENTS.md](AGENTS.md),
этапы — [ARCHITECTURE.md](ARCHITECTURE.md). Коммиты только в ветках, через PR.
