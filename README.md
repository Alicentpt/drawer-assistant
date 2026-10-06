# Drawer Assistant

Джессика — дерзкая Telegram-напарница художницы на Hermes: заказы, дедлайны,
напоминания, референсы с изображениями и генерация через удалённый ComfyUI.
Разговорная речь, мат к месту; без давления на собеседниц.

## На этом ноутбуке

Токен, группа и SSH уже записаны в локальный `.env`; ChatGPT подключён к профилю.
Команды из папки `drawer-assistant`, в новом PowerShell:

```powershell
uv run drawer-setup      # установить/обновить Джессику и применить .env
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
перечитает настройки профиля. После обновления кода плагина перезапусти gateway:
`hermes.cmd --profile default gateway stop`, затем `uv run poe bot`.
Не редактируй копии токена вручную.

Настройщик копирует персону и плагин, включает инструменты для Telegram,
создаёт один cron `drawer-reminders` каждые 60 секунд без LLM. Повторный запуск
обновляет этот job. Исходная персона сохраняется в `SOUL.before-drawer.md`.
Штатные файловые инструменты и навыки включены, рабочая папка — профиль
`drawer-assistant`. Джессика может читать и править свой SOUL.md, память, навыки
и заметки. Рабочий каталог не является ограничением доступа на уровне ОС;
ограничение на собственные документы задано в персоне, защиты Hermes сохранены.
Обновление заменяет SOUL.md только если он совпадает с предыдущим шаблоном;
пользовательские правки остаются, новый шаблон доступен в `plugins/drawer/SOUL.md`.
Python для cron — `.venv` проекта: после переноса папки повтори настройку.

## Что написать Джессике

Упомяни бота перед фразой или ответь на его сообщение:

- «Создай заказ: портрет для Ани, срок 9 октября 2026 в 18:00 по Калининграду».
- «Что у меня по заказам? Перенеси портрет на 12 октября, 18:00».
- «Напомни про портрет 11 октября в 12:00». «Портрет готов — закрой заказ».
- «Сохрани референс к портрету: https://example.com/art — нравится палитра».
- Прикрепи фото: «Сохрани эту картинку к портрету. Взять позу и свет».
- «Покажи сохранённые картинки к портрету».
- «Сгенерируй референс путешественницы в синем пальто через Z-Image».
- «Сделай ещё вариант через Flux2 Klein».

Напоминание по умолчанию приходит в дедлайн; раннее время укажи отдельно.
Перенос заменяет старое напоминание, закрытие/отмена отключают его. После сна
ноутбука обрабатываются пропущенные сроки. Повтор после ошибки — с задержкой
от минуты до часа; состояние отправки хранится в SQLite. Потеря ответа Telegram
после успешной отправки может привести к повторному сообщению.

`drawer_orders` — создать/изменить/показать; `drawer_references` — сохранить/показать
картинки и ссылки с заметками; `drawer_zimage` / `drawer_flux_klein` — GPU-модели.
Поиск и ChatGPT-генерация используют штатные инструменты Hermes.
`drawer_persona` показывает текущий `SOUL.md` установленного профиля. Он доступен
по просьбе «покажи свою персону» и не принимает произвольные пути. Постоянная
память Hermes хранится отдельно; её обновление не редактирует файл персоны.
GPU-инструмент пока поддерживает текст → картинка, 1024×1024, один кадр.
Неизвестный результат HTTP-отправки не создаёт автоматическую повторную генерацию.
Картинку Джессика отдаёт через MEDIA-маркер Hermes с моделью и временем выполнения
ComfyUI в секундах (без очереди и передачи файла). Замер сохраняется в SQLite;
если сервер не прислал timestamps, время считается неизвестным.

Данные по умолчанию: `drawer-data` в профиле; путь меняется через `DRAWER_DATA_DIR`.
Для бэкапа скопируй эту папку при остановленном gateway. Состояние таймера:
`hermes.cmd --profile drawer-assistant cron list --all`.

Изображения референсов хранятся **байтами BLOB в SQLite**, с SHA-256, MIME,
размером и именем файла; одинаковые байты не дублируются. `save` принимает
`file` — абсолютный путь вложения Telegram/результата генерации, либо `image_url` —
прямую ссылку на картинку. Поддерживаются PNG/JPEG/WebP/GIF до 20 МиБ без перекодирования.
Проверяется сигнатура формата; полное декодирование изображения не выполняется.
`storage=blob` подтверждает запись байтов. Старые ссылки и `url` на страницу
остаются `link_only`, пока отдельно не сохранена картинка. Миграция их не скачивает.
`list` отдаёт метаданные, `get(id)` восстанавливает файл из БД для отправки или
генератора. `reference-exports/` — восстанавливаемые копии, их потеря не теряет оригинал
в БД. Изменение заметки сохраняет картинку; новый file/image_url заменяет её.
Telegram-фото сохраняется в полученном качестве; для исходного файла отправляй
изображение документом. Сохранение происходит по просьбе через инструмент, а не
автоматически для каждого сообщения. Дополнительные ключи и настройки не нужны.

`-X utf8` при установке Git hook нужен для кириллицы в пути Windows.
Для PR нужен вход в [GitHub CLI](https://cli.github.com/) через `gh auth login`
либо создание PR в браузере; SSH-ключ сам по себе CLI не авторизует.

## Alibaba: отдельный инструмент для каждой модели

| Инструмент | Модель | Референсы | Negative prompt |
| --- | --- | --- | --- |
| `drawer_qwen_image_3` | Qwen Image 3.0 | До 3 | Да |
| `drawer_qwen_image_3_pro` | Qwen Image 3.0 Pro | До 3 | Да |
| `drawer_qwen_image_2_1_pro` | Qwen Image 2.1 Pro | До 10 | Нет |

Генерация и редактирование — одним и тем же инструментом: без images это
text-to-image, с images — правка/композиция по референсам. images принимает
HTTPS-ссылки или абсолютные локальные пути, по одному на строку. Локальные
PNG/JPEG/WebP до 10 MiB кодируются в Base64; исходники передаются в Alibaba.
Порядок соответствует «image 1», «image 2». Это референсы, а не жёсткий ControlNet.

Заполни `DASHSCOPE_API_KEY` и `DASHSCOPE_BASE_URL` в `.env`, затем выполни
`uv run drawer-setup` и перезапусти общий gateway. Получение ключа:
[Alibaba Model Studio](https://www.alibabacloud.com/help/en/model-studio/get-api-key).
Ключ и endpoint должны совпадать по региону; в примере Singapore. Ключ остаётся
в .env профиля, не попадает в settings.json, Git, промпт или Telegram.

Промпт для группы с двумя прикреплёнными картинками:

> Через Qwen Image 3.0 сделай референс: поза и композиция с первой картинки,
> персонажи и палитра со второй. Две взрослые ведьмы спорят за одну метлу,
> игривый Хэллоуин. Positive: dynamic composition, warm pumpkin light.
> Negative: blurred hands, unreadable text. Не дополняй промпт автоматически.

По умолчанию один результат, 1024×1024, `prompt_extend=false`, пустой negative.
Можно задать size, seed и включить prompt_extend. Не меняем провайдера молча.
start сохраняет намерение до платного запроса; status сохраняет PNG и серверные
секунды исполнения (без очереди/скачивания). submitting после потери ответа не
повторяет генерацию. Задания/ссылки Alibaba живут 24 часа: опроси результат вовремя.
Пока status не вызван, отдельный фоновый загрузчик картинки не работает.

По официальным тарифам Singapore на 06.10.2026: Qwen 3.0 — $0.03 за результат;
3.0 Pro — $0.04 (1K) / $0.075 (2K); у обеих дополнительно $0.003 за входную
картинку. Qwen 2.1 Pro — $0.04 за результат. Тарифы могут меняться:
[3.0](https://www.alibabacloud.com/help/en/model-studio/qwen-image-3-0),
[3.0 Pro](https://www.alibabacloud.com/help/en/model-studio/qwen-image-3-0-pro),
[2.1 Pro](https://www.alibabacloud.com/help/en/model-studio/qwen-image-2-1-pro).
Все запросы проходят модерацию Alibaba; negative prompt не отключает фильтры.

Используется [DashScope async API](https://www.alibabacloud.com/help/en/model-studio/qwen-image-generation-and-editing-api-reference).
У Alibaba также есть [Wan 2.7](https://www.alibabacloud.com/help/en/model-studio/wan-image-generation-and-editing-api-reference):
до 9 референсов, без отдельного negative, Pro умеет 4K text-to-image.
Wan пока не подключён. Моками проверены протокол и сбои; живое качество/скорость
Qwen до добавления ключа не подтверждены.

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
.venv/bin/python main.py --listen 127.0.0.1 --port 8188 --disable-auto-launch --gpu-only --disable-async-offload --cache-none --disable-cuda-malloc
```

На L40 проверены настоящая генерация и получение PNG новым инструментом.
Z-Image W4A8 + abliterated Qwen: около **5,73 ГБ VRAM**, Flux2 Klein mixed
INT4/INT8 + тот же encoder: около **6,74 ГБ**. Выгрузки моделей на CPU нет;
клиент требует `--gpu-only`. Это измерения L40, не гарантия для RTX 3080.
Модели, точные версии и замеры — [comfyui/README.md](comfyui/README.md).
Версии: ComfyUI `7ddf9a4f8aef66bca2eda1be2b936965be12b3b0`, Python 3.12.13,
PyTorch 2.14.1+cu130. Снимок зависимостей: `~/comfyui-installed.txt` на сервере;
лог текущего запуска: `~/comfyui.log`.

Облачный image provider настроен на `openai-codex`; OAuth и текстовый ответ
проверены. Генерацию и правку через этот аккаунт ещё нужно проверить отдельно.
Доступ Telegram подтверждён пользователем: в группе отвечает, в личке молчит.

## Разработка

```powershell
uv run poe check
uv run poe audit
```

Тестируем настройки, переносы/отмены сроков, повтор доставки, GPU-only и ошибки
API. Также проверяем Ruff, типизацию и документацию;
GitHub Actions повторяет их на Windows/Linux. Правила — [AGENTS.md](AGENTS.md),
этапы — [ARCHITECTURE.md](ARCHITECTURE.md). Коммиты только в ветках, через PR.
