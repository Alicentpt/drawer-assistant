# Nova Anime AM v2.0 на RunPod

Пресет тот же, что на Windows: Nova v2.0 + Qwen3 0.6B Base + Qwen Image VAE,
512×512, 25 шагов, CFG 5, Euler ancestral / simple. ComfyUI 0.39.0, commit
`b0b743566f65daafc423b4fea8a2fbda94b3384a`; frontend 1.53.10.

1. В работающий под `runpod/comfyui:1.3.3-comfyuiv0.30.0-cuda13.0` загрузить
   `runpod-start.sh`, `models.json` и `nova-anime-am-v20-text-img2img.ui.json`
   из этой папки в `/workspace/drawer-assistant`.
2. Остановить прежний процесс ComfyUI, чтобы освободить 8188. Проверить PID перед
   остановкой; не останавливать весь под ради освобождения порта.
3. Запустить `bash /workspace/drawer-assistant/runpod-start.sh`.
   Скрипт скачивает около 5,6 ГБ весов по закреплённым revisions и сверяет SHA-256.
4. Для запуска после рестарта: entrypoint `/bin/bash`, cmd
   `/workspace/drawer-assistant/runpod-start.sh`. Порт пода — `8188/http`.
5. В `.env` ноутбука: `COMFYUI_ANIMA_BASE_URL=https://POD_ID-8188.proxy.runpod.net`.
   Выполнить `uv run drawer-setup`, перезапустить общий gateway.

Python-окружение, ComfyUI и рабочие копии моделей — на локальном диске `/opt`.
RunPod Global Store в `/workspace` запрещает некоторые операции с symlink и
chmod, поэтому обычный `.venv` внутри `/workspace` может не создаться.
Скрипт сохраняет копии весов, входы, выходы и UI workflow в
`/workspace/drawer-assistant`; после пересоздания контейнера восстанавливает `/opt`.
Удаление пода/хранилища не является резервным копированием.

Все компоненты работают на GPU: `--gpu-only --disable-async-offload
--disable-pinned-memory`, без custom nodes. HTTP-клиент указывает
`User-Agent: DrawerAssistant/0.1`: RunPod proxy отклонял стандартный Python UA
с HTTP 403 / Cloudflare 1010, тот же запрос с именем приложения вернул HTTP 200.
Для UI img2img загрузить свой референс в LoadImage; техническую картинку
`jessica_anima_reference_placeholder.png` можно заменить первым тестовым PNG.
Переключатель референса и denoise описаны в [README](README.md).

Проверка: `/system_stats` содержит `--gpu-only`, `/object_info` видит все три
файла моделей, текстовая генерация завершается PNG, повторный запуск не копит
очередь. В Telegram передавать модель и `generation_seconds`, холодный и тёплый
запуски обозначать отдельно. Результат на A6000 не является замером RTX 3080.

## Живая проверка 07.10.2026

RTX A6000 48 ГБ; PyTorch 2.10.0+cu130, драйвер 595.91.07.
После смены entrypoint контейнер пересоздан: файлы `/workspace` сохранились,
ComfyUI восстановился и выполнил генерации. Telegram подтвердил доставку трёх PNG.

| Тест | Выполнение ComfyUI, с | Полный запрос, с |
| --- | ---: | ---: |
| Текст, первый запуск с загрузкой моделей | 11,28 | 17,53 |
| Текст, модели уже загружены | 2,67 | 7,07 |
| Img2img, denoise 0,6, референс — первый результат | 2,76 | не измерен |

Server time — события `execution_start` → `execution_success`; полный запрос
включает HTTP, опрос и скачивание PNG. Наблюдавшийся пик двух текстовых тестов —
6,02 ГБ (10⁹ байт), `vram_total - vram_free`, опрос раз в секунду плюс HTTP.
Это не принудительный предел и может пропускать короткие пики. Все PNG 512×512.
Логи подтверждают загрузку всех весов целиком и encoder/VAE на `cuda:0`.
UI JSON сохранён в Workflows, HTML интерфейса отвечает HTTP 200; обе ветки
проверены через API. Визуальная проверка Chrome заблокирована клиентом
(`ERR_BLOCKED_BY_CLIENT`), поэтому её не считаем выполненной.
