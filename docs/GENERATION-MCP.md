# Генерация Джессики: где читать и что менять

**Hermes на EC2 → MCP через stdio → ComfyUI художницы через Tailscale.**
MCP — отдельный дочерний процесс без сетевого порта. Скиллы объясняют агенту,
как пользоваться инструментами; MCP публикует схемы и выполняет запросы.
Заказы, персона и напоминания остаются в Hermes и нашем обычном плагине.

## Карта файлов репозитория

Все пути ниже относительно корня `drawer-assistant`.

| Что менять | Файл |
|---|---|
| ВСЕ схемы и описания инструментов генерации | `src/drawer_assistant/generation/tools.json` |
| MCP stdio, discovery, формат результатов/ошибок | `src/drawer_assistant/mcp_generation.py` |
| Defaults и positive/negative | `src/drawer_assistant/generation/presets.py` |
| Применение настроек, stretch, графы нескольких refs | `src/drawer_assistant/generation/graphs.py` |
| Четыре API workflow | `src/drawer_assistant/generation/workflows/*.api.json` |
| Запуск, защита от дублей, статус, скачивание preview | `src/drawer_assistant/generation/jobs.py` |
| Defaults/переопределения и последовательность batch | `src/drawer_assistant/generation/batches.py` |
| HTTP загрузка/скачивание изображений | `src/drawer_assistant/generation/transport.py` |
| Проверка нод, моделей, sampler, GPU-only | `src/drawer_assistant/generation/health.py` |
| Валидация и диспетчеризация вызовов | `src/drawer_assistant/generation/service.py` |
| Сборщик ImageMagick | `src/drawer_assistant/image_sheet.py` |
| Скилл промптинга | `src/drawer_assistant/skills/drawer-anima/SKILL.md` |
| Скилл выбора/настройки controls | `src/drawer_assistant/skills/drawer-anima-controls/SKILL.md` |
| Скилл batch, статусов, коллажей и отправки | `src/drawer_assistant/skills/drawer-images/SKILL.md` |
| ВСЕ наши остальные скиллы | `src/drawer_assistant/skills/*/SKILL.md` |
| ВСЕ native инструменты заказов/референсов/персоны | `src/drawer_assistant/tools.json` + `plugin.py` |
| Общие краткие правила агента | `src/drawer_assistant/.hermes.md` |
| Шаблон личности | `src/drawer_assistant/SOUL.md` |
| Подключение MCP и установка навыков | `src/drawer_assistant/installation.py` |
| Docker и закреплённые зависимости MCP | `deployment/Dockerfile`, `deployment/mcp-requirements.txt`, `uv.lock` |
| Регрессионные проверки | `tests/test_generation_mcp.py`, `tests/test_postgres.py` |

Старые native `drawer_anima`, `drawer_anima_batch`, `drawer_image_sheet` сохраняются
для совместимости кода/истории, но **не регистрируются** при `generation_mcp=true`.
Старые `experiments/anima-controls` — архив экспериментов, не defaults нового MCP.
Не меняйте их в расчёте изменить рабочий генератор.

## Инструменты MCP

| Инструмент | Назначение |
|---|---|
| `anima_generate` | Один кадр по тексту |
| `anima_style` | Один кадр с одним стилевым референсом |
| `anima_character` | Один кадр с 1–3 отдельными референсами внешности |
| `anima_depth` | Один кадр с одним референсом композиции + карта глубины |
| `anima_batch` | 2–16 кадров одного режима с индивидуальными настройками |
| `generation_status` | Статус, скачивание результатов, продвижение пачки |
| `image_sheet` | Коллаж из готовых локальных картинок |
| `generation_health` | Проверка сервера по режимам без GPU-генерации |

Hermes добавляет собственный префикс к именам. Скиллы предлагают искать инструмент
через `tool_search`, а не угадывать имя. All In One/Pose отсутствуют.

## Defaults и ручные настройки

Одна diffusion-модель: **novaAnimeAM_v20.safetensors**, storage FP8 e4m3fn.
Qwen3 0.6B Base и Qwen Image VAE; Anima Base не используется.

Общие defaults: **512×512 · CFG 7 · 45 steps · res_multistep/simple · denoise 1**.
Каждый референс сначала проходит ImageScale nearest-exact до этих размеров,
с растяжением/сжатием, crop=disabled, без полей. Пропорции могут измениться.
Для Character несколько refs объединяются как отдельные latent frames,
не как один нарисованный коллаж. Внутренний fit_mode также stretch.

Style: strength **0.65**, ref_image_size=512, siglip_layer=-1,
ip_cfg_separate=false, gray_null=false, use_lora=true. IP CFG следует общему CFG.
Ближе к 1 и выше чаще заимствуется композиция, ниже 0.65 стиль может быть слабым.
Нужно описывать манеру английским текстом дополнительно к изображению.

Character: strength **1.3**, start=0, end=1, cond_only=true, ref_timestep=0.
Пробные силы: **1.2 / 1.3 / 1.5 / 1.6 / 1.8 / 2**. Ближе к 1 может сильнее
заимствоваться стиль; повышение может усиливать персонажа, но не гарантирует лицо.
В промпт добавляйте внешность/волосы/глаза/одежду и характерные детали.

Depth: strength **1**, start=**0**, end=**0.6**. Карта управляет объёмом,
композицией и приблизительной позой; это не точный скелет.
start/end задают долю процесса denoising, где действует контроль. Раннее
отключение может уменьшать навязывание шумных деталей в результат.
**Они не фильтруют пиксели карты.** Поэтому возвращаются точная использованная
карта `depth_preview`/`depth_media` и фактическое окно `depth_window` отдельно.

AuraFlow shift можно задать явно; общий default 3. Не менять несколько параметров
одновременно при сравнении. При нехватке VRAM нет CPU/RAM fallback.
Границы параметров в схеме — техническая валидация, не доказательство бюджета 9 ГБ.

## Positive / negative можно изменить

Точные строки — в `presets.py` и `drawer-anima/SKILL.md`.
Positive включает painting; negative больше не содержит painting/cartoon/old.
Остальные запрошенные пользователем теги сохранены.

`options.positive_prefix` и `options.negative_prompt` **полностью заменяют**
соответствующий список. Пустая строка отключает его. Отсутствующее поле сохраняет
default. Если нужен pencil drawing, агент может убрать graphite/crayon из negative
и заменить painting в positive, не ломая сюжет. Фактические тексты есть в результате.

## Batch: настройки каждого кадра

```json
{
  "request_id": "style-strength-comparison-01",
  "mode": "style",
  "references": ["/opt/data/profiles/drawer-assistant/reference.png"],
  "defaults": {"seed": 42, "cfg": 7, "steps": 45},
  "frames": [
    {"prompt": "an adult woman reading, digital painting", "options": {"strength": 0.5}},
    {"prompt": "an adult woman reading, digital painting", "options": {"strength": 0.65}},
    {"prompt": "an adult woman reading, digital painting", "options": {"strength": 0.85}}
  ],
  "columns": 3,
  "labels": ["0.50", "0.65", "0.85"]
}
```

Каждый frame может заменить refs и любые подходящие его mode параметры:
seed, cfg, steps, размеры, sampler, denoise, shift, strength, start/end и preset.
Общие и личные options сливаются по ключам; refs кадра заменяют общий список целиком.
strength/start/end для неподходящего режима отклоняются, не игнорируются.
Один mode на пачку. `assemble=false` оставляет отдельные результаты без сетки.

Пачка запускает следующий кадр при `generation_status`; фонового worker нет.
При перезапуске клиента продолжить с сохранённым request_id. При submitting
не делать новый POST: сначала выяснить, принял ли ComfyUI предыдущее задание.
Готовые кадры сохраняются при ошибке других; полный коллаж появляется только
после успеха всей пачки. Время GPU/сборки выводится отдельно от очереди и передачи.

## Что хранится

PostgreSQL/SQLite, миграция **7**, новая таблица `generation_tasks`.
Существующие заказы, ссылки, BYTEA, старые задания и напоминания не переписываются.
Сохранены нормализованный запрос, endpoint, seed, prompt_id, состояние и результат.
Перед удалённой отправкой сохраняется intent. Повторы с другим запросом запрещены.

Байты входов кешируются по SHA-256 в `drawer-data/generation-inputs`,
результаты/карты — `drawer-data/generation-results`, коллажи — `image-sheets`.
Бинарные референсы заказов по-прежнему хранятся отдельно в RDS через assets.
MCP принимает файлы, которые Hermes получает через drawer_references get,
или локальные вложения Telegram. Не передаёт Windows-пути удалённому серверу.

## Где рабочие файлы на EC2

Корень проекта: `/home/ubuntu/drawer-assistant`.
Профиль внутри контейнера: `/opt/data/profiles/drawer-assistant`.
На хосте он расположен в `deployment/state/hermes/profiles/drawer-assistant`.

- `SOUL.md` — действующая персона, пользовательские изменения сохраняются.
- `.hermes.md` — действующие краткие правила.
- `plugins/drawer/skills/*/SKILL.md` — установленные наши навыки.
- `skills/` — дополнительные пользовательские навыки, setup их не стирает.
- `config.yaml` → mcp_servers.drawer_generation — команда stdio и её аргументы.
- `config.yaml` → platform_toolsets.telegram — включён drawer_generation (в реестре mcp-drawer_generation).
- `plugins/drawer/settings.json` — endpoint/cache, generation_mcp=true.
- `.env` — секреты профиля, **не отправлять в чат**.
- `logs/mcp-stderr.log` — диагностический stderr MCP.

MCP Python в Docker: `/opt/drawer-mcp/bin/python`, SDK **2.2.0**;
код установлен в его `lib/python3.14/site-packages/drawer_assistant`.
Копии Python-модулей внутри `plugins/drawer` относятся к native плагину;
их правка не обновляет код MCP в отдельном окружении. Менять исходник репы
и пересобирать образ — основной способ обновления кода генерации.
Окружение Hermes и его pinned dependencies сохраняются отдельно.
Для cron установлен no_mcp: генерация не добавляется автоматически к сводке.
Вне Docker setup использует текущий Python; можно задать
`DRAWER_GENERATION_PYTHON` до setup для отдельного окружения.

Штатные инструменты самого Hermes находятся в `/opt/hermes/tools` и
`/opt/hermes/plugins`; их состав зависит от версии Hermes и config.toolsets.
Они не являются частью нашего MCP. Их обновляют вместе с Hermes, не копированием
случайных файлов в профиль. Для проверки доступности нужен реальный tool discovery.

## Как вносить изменения

**Код/схемы/workflow:** изменить исходник в репе → `uv run poe check` и audit →
пересобрать Docker → обновить установленный plugin/skills → перезапустить контейнер.
Сначала сохранить профиль и базу. Не запускать второй gateway для проверки MCP.
Для обновления MCP dependencies: `uv lock`, затем экспорт с хешами:

```powershell
uv export --quiet --locked --no-dev --no-emit-project --format requirements-txt --output-file deployment/mcp-requirements.txt
```

**Скиллы:** можно править рабочую копию в профиле, но затем перенести правку
в исходник репозитория — иначе следующая установка заменит bundled skill.
У MCP нет отдельного скрытого набора skills: это три обычных навыка Hermes выше.

Локальный Telegram gateway на ноутбуке не запускать: рабочий бот уже на EC2.
Локальные tests/MCP разрешены с отдельным тестовым settings и базой.

Резервная копия переключения на EC2:
`/home/ubuntu/.local/state/drawer-mcp-cutover-20261017/hermes-before.tar.gz`.
Снимок прежних девяти таблиц — внутри volume: `mcp-backups/20261017/postgres-before.json`.
После миграции 7 прежний образ с поддержкой только схемы 6 нельзя просто запустить
против той же базы. Для отката нужно согласовать версию кода и схему;
не удалять новые задания или новые записи заказов ради понижения версии.

## Установка нод художнице

Комплект: `deployment/install-anima-controls.ps1` плюс `revisions.json`,
`style-gpu-only.patch`, `depth-dtype.patch` из `experiments/anima-controls`.
В закрытом ComfyUI запустить PowerShell-скрипт. Он спрашивает папку ComfyUI
и его Python, не ставит новый Python, сохраняет текущие версии torch/numpy.
Другую версию существующих нод/весов не перезаписывает.

На момент подготовки прежний Character источник отвечает HTTP 401.
Для уже скачанной разрешённой копии репозитория: `-CharacterSource "D:\Anima-InContext-Character"`.
В папке нужны comfyui-anima-incontext и anima-incontext-character.safetensors.
LoRA проверяется SHA-256. При недоступности скачивания скрипт завершится кодом 2
и не объявит Character готовым. Не присылайте токены в чат.

После установки перезапустить ComfyUI GPU-only и проверить generation_health.
Успех API/моков не доказывает работу адаптеров или качество на RTX 3080;
живые результаты каждого режима фиксируются отдельно.

Протокол: [официальный Python SDK](https://github.com/modelcontextprotocol/python-sdk).
Описание stdio: [MCP Build server](https://modelcontextprotocol.io/docs/develop/build-server).
