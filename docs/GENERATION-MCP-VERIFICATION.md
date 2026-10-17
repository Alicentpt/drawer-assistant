# Проверка MCP генерации — 17.10.2026

## Подтверждено

- Полный `uv run poe check`: **134 passed**, 3 live PostgreSQL tests skipped локально.
  Ruff, Mypy strict, Pyright strict, Pylint, Google docstrings/coverage,
  Vulture, deptry и Bandit прошли.
- `uv run poe audit`: известных уязвимостей зависимостей проекта не найдено.
  MCP SDK обновлён с 2.0.0 до 2.2.0, runtime отделён от Hermes.
- На отдельном PostgreSQL 17: **4 passed**, включая миграцию 6 → 7,
  сохранность старых заказов, целостность переноса и блокировку reminder worker.
- Официальный MCP клиент открывает реальный subprocess, выполняет handshake,
  получает восемь инструментов; неверные аргументы и недоступный сервер дают error.
- Моки транспорта: повтор не дублирует POST; потерянное подтверждение остаётся
  submitting; batch восстанавливает последовательность и настройки кадров.
- Проверены графы раннего stretch для всех refs, Character с двумя latent inputs,
  заменяемые positive/negative, отдельный сохранённый выход depth map и окно влияния.
- В рабочем Hermes на EC2 сама модель вызвала `skill_view`, прочла новый
  drawer-anima-controls, нашла MCP через tool_search/tool_describe и вызвала
  `mcp__drawer_generation__image_sheet`. Получен настоящий PNG из двух тестовых
  цветных изображений, время ImageMagick **0.22 с**, финальный ответ `MCP_OK`.
  Сообщений в Telegram и новых GPU-заданий эта проверка не отправляла.
- После переключения Telegram connected; SOUL и cron/jobs.json сохранены побайтно.
  В RDS 5 заказов; новая таблица generation_tasks добавлена без перезаписи старых.
  Отдельный прежний Hermes хоста остался active.

## Пока НЕ подтверждено

При первом чтении ПК художницы отвечал, GPU RTX 3080, --gpu-only, Nova установлена.
API не содержал AnimaIPAdapterApply, AnimaInContextApply и Advanced ControlNet.
Подготовлен PowerShell installer закреплённых нод/весов и GPU-only патчей.
Исторический источник Character на Hugging Face возвращает 401;
для него предусмотрена установка из уже скачанной разрешённой локальной копии.

При последующем живом MCP тесте Tailscale ping отвечал, но HTTPS ComfyUI
завершался по таймауту. Health и text start вернули ошибку до отправки GPU job.
Поэтому **новые 45-step text/style/character/depth и GPU batch на 3080 ещё
не подтверждены**, время GPU и VRAM для них не измерены.

Следующий шаг: установить ноды, запустить ComfyUI, generation_health,
по одному живому запуску каждого режима и reference batch. Проверить исходную
depth карту, фактические настройки, GPU-only и пик памяти. Успех протокола,
сборки коллажей и моков не заменяет этот тест и не доказывает качество переноса.
