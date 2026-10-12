# Пять экспериментальных workflow Anima

Статус: 12.10.2026 испытаны на RTX 3090, включая запуск All In One из UI.
На RTX 3080 ещё не испытаны. Замеры — в [TEST-REPORT.md](TEST-REPORT.md).
Разбор стиля, независимого CFG и Character по 56 новым прогонам —
[DEEP-TEST-REPORT.md](DEEP-TEST-REPORT.md).
Рабочие инструменты Hermes пока используют прежний text-to-image. Эти файлы
не входят в allowlist генератора и не подменяют действующий workflow.

Все пять используют закреплённый файл Nova Anime AM, Qwen3 0.6B Base и Qwen Image VAE.
Standalone: 512×640; новый All In One: 512×512 для бюджета памяти всех четырёх
веток. Один кадр на GPU job. Quality prefix и negative сохранены.
Вес diffusion хранится в FP8 e4m3fn; вычисления и все модели остаются на GPU.
FP8 выбран после замеров: обычные веса с LoRA превышали 9 ГиБ на 3090.
Предел для конкретной RTX 3080 и качество переноса требуют дальнейшей проверки.

| API-граф | Вход | Дополнительные ноды и веса |
|---|---|---|
| `style.api.json` | образец стиля | Anima IP-Adapter, SigLIP2, `ip_adapter-Character_Reference-10.safetensors` |
| `character.api.json` | персонаж | Anima InContext, `anima-incontext-character.safetensors` |
| `depth.api.json` | фото/рисунок композиции | DepthAnythingV2 small, Advanced ControlNet с поддержкой Anima VACE, `anima-vace-depth.safetensors` |
| `pose.api.json` | фото/рисунок позы | AnimaPoseControl, AnimaControlApply, `anima_pose_preview2.safetensors`, два ONNX детектора |
| `all-in-one.api.json` | стиль + персонаж + поза + глубина | четыре независимых переключателя, зависимости всех четырёх веток |

## Открыть в UI

Файлы `*.ui.json` — для редактора, `*.api.json` — для HTTP `/prompt`.
На тестовом сервере они сохранены в `user/default/workflows/Anima-Controls`:
слева **Рабочие процессы → Anima-Controls → all-in-one-v2-pose-depth.ui**.
Старый `all-in-one.ui` сохранён отдельно; v2 открывается новой вкладкой.
Для другого компьютера перетащить UI JSON в ComfyUI, установить закреплённые
ноды/веса и загрузить свои изображения в четыре LoadImage. Наши тестовые файлы
на сервере называются `jessica-style.png`, `jessica-character.png`,
`jessica-composition.png`; в Git самих изображений нет.

Переключатели **STYLE / CHARACTER / POSE / DEPTH — ON / OFF**:
`true` включает ветку, `false` обходит её целиком вместе с LoRA.
Все `false` дают обычный text-to-image. Силу менять в Apply нодах.
Depth по умолчанию выключен; поза и глубина могут работать вместе или отдельно.
Depth принимает собственную картинку, она не обязана совпадать с референсом позы.
Для API изменить boolean `switch` у узлов 150 / 250 / 350 и `value` у узла 440.
У Depth один boolean одновременно переключает positive и negative conditioning.
Ноды и веса нужны даже для выключенных веток: валидация ComfyUI проверяет полный
граф. LoadImage должны указывать на существующие файлы. Lazy switch исключает
выполнение ветки, но не отменяет валидацию. При сборке API-графа без референсов
следует удалять выключенные ветки.

Пересобрать All In One из отдельных API-графов:
`uv run python -m drawer_assistant.anima_graph`.
AuraFlow shift стоит один раз после всей цепочки моделей; размер latent,
AnimaRefEncode и ImageScale позы согласован. Не менять один из них отдельно.

Перенос стиля через выбранный IP-Adapter — гипотеза для проверки. Доступный файл
весов называется Character Reference; README нод заявляет style transfer,
но это не доказательство независимого переноса стиля без копирования персонажа.
Depth контролирует глубину сцены, Pose — скелет; это отдельные тесты.

## Источники и установка на компьютере с ComfyUI

Точные ревизии кода и весов закреплены в `revisions.json`. Не обновлять всё
окружение ComfyUI ради этих экспериментов; сначала проверить текущие версии.

- [IP-Adapter](https://github.com/LuciferTC9527/ComfyUI-Anima_IP-Adapter): каталог
  в `custom_nodes`, веса из [LuciferTC](https://huggingface.co/LuciferTC/Anima-IP-Adapter)
  в `models/ipadapter`; [SigLIP2](https://huggingface.co/google/siglip2-base-patch16-512)
  в `models/siglip2/siglip2-base-patch16-512`. Автозагрузка в графе выключена.
- [InContext Character](https://huggingface.co/darask0/Anima-InContext-Character):
  `comfyui-anima-incontext` в `custom_nodes`, safetensors в `models/loras`.
- [VACE Depth](https://huggingface.co/TaihoC/Anima-ControlNet-VACE-Depth):
  safetensors в `models/controlnet`;
  [Advanced ControlNet, исправленная ветка](https://github.com/PineCookie/ComfyUI-Advanced-ControlNet/tree/fix/anima-vace-hardening)
  в `custom_nodes`. Не загружать одновременно оригинал и форк с одинаковыми IDs.
  [ControlNet Aux](https://github.com/Fannovel16/comfyui_controlnet_aux) даёт
  `DepthAnythingV2Preprocessor`; нужен `depth_anything_v2_vits.pth`.
- [Pose Preview-2](https://huggingface.co/Claquasse/Anima-Control-Pose): оба каталога
  из `comfyui/` в `custom_nodes`, safetensors в `models/loras`; детекторы из
  `detector/` в кеш rtmlib согласно README автора.
- [KJNodes](https://github.com/kijai/ComfyUI-KJNodes): закреплённая ревизия
  в `custom_nodes`, даёт `LazySwitchKJ` для All In One.

Зависимости каждой ноды устанавливаются Python-интерпретатором именно ComfyUI.
Веса не загружаются на ноутбук агента. Перезапуск ComfyUI — после установки
зависимостей и GPU-only правок; затем проверить `/object_info` и `/system_stats`.
После установки rtmlib удалить CPU-пакет `onnxruntime` и установить совместимый
`onnxruntime-gpu`: два пакета с одним импортом могут перекрывать друг друга.

## GPU-only

ComfyUI должен работать с `--gpu-only`. Одного флага для сторонних нод недостаточно:

- IP-Adapter вызывает `siglip_encoder.to("cpu")`. `style-gpu-only.patch` убирает
  эти три выгрузки в закреплённом `nodes.py`; SigLIP остаётся в VRAM.
- Pose изначально создаёт ONNX детектор на CPU. `pose-gpu-only.patch` меняет
  закреплённый `ComfyUI-anima-pose-control/detect.py` на CUDA и запрещает запуск
  без `CUDAExecutionProvider`. Нужен совместимый `onnxruntime-gpu`, а не CPU пакет.
- `pose-apply-gpu.patch` использует compute dtype вместо FP8 storage dtype и
  применяет скелет только к генерируемому кадру, сохраняя InContext reference.
- `style-gpu-only.patch` также переводит LoRA на ModelPatcher: исходные постоянные
  обёртки меняли общие слои и загрязняли последующие задания. Добавлена поддержка
  compute dtype для FP8. Остаточные IP-модули остаются в VRAM, но без IP-токенов
  не участвуют в расчёте. Переключение проверено сравнением пикселей.
- `depth-dtype.patch` приводит вход base patch embedding к compute dtype;
  исходник падал на несовпадении fp32/bf16, затем FP8/bf16. Также ограничивает
  depth hints генерируемым кадром, не меняя чистые reference frames InContext.

До применения сверить SHA-256 исходного файла из `revisions.json`, затем проверить
патч через `git apply --check` из каталога соответствующей ноды. Не применять
к другой версии вслепую.
Патчи применены на тестовом поде и проверены живыми прогонами. Это ещё не аудит
всех сторонних нод: --gpu-only, CUDA ONNX и отсутствие CPU model offload проверены,
но служебные shape-операции ONNX могут идти на CPU. При OOM уменьшаем размер/
меняем адаптер, без CPU fallback.

Настройка вручную описана в [TUNING.md](TUNING.md). Диапазоны — стартовые
экспериментальные ориентиры, не обещание точного результата.

## Референсы и проверка

Для предметной проверки нужны отдельные изображения:

1. `character.png`: один персонаж, хорошо видны лицо и одежда; желательно полный
   рост. Дополнительный крупный план лица полезен для следующего теста с двумя refs.
2. `style.png`: характерная линия, палитра, свет и фактура; желательно другая сцена
   и другой персонаж, чтобы отличить перенос стиля от копирования содержания.
3. `composition.png`: нужная поза и ракурс, тело не обрезано и не перекрыто.
   Один файл подходит для тестов Pose и Depth; для Depth полезен читаемый фон.

Через ComfyUI загрузить их как `jessica-<имя>.png`; в API-графах это пути
входов на сервере, а не локальные Windows-пути агента. Можно заменить значения
`LoadImage.image` на имена, фактически возвращённые загрузкой.

Сначала прогон каждого графа отдельно. Затем сравнение с базовым text-to-image
на одинаковых seed, промпте, sampler, steps, CFG и размере: в baseline убрать
ветку адаптера целиком, включая LoRA, а не только выставить strength=0.
Для style/character изменить сцену относительно референса.
Сохранить вход, полный API-граф, результат, статус и серверное время, пик VRAM.
Для строгой проверки позы сначала посмотреть полученный скелет; на рисунках
детектор может ошибаться. Никаких выводов о переносе по одному удачному кадру.

## Следующий этап: четыре инструмента и общие батчи

После проверки графов подключить `drawer_anima_style`, `drawer_anima_character`,
`drawer_anima_depth`, `drawer_anima_pose`. Каждый привязан к своему workflow.
Один `prompt` — один кадр; массив `prompts` — независимые задания и общий лист.
Использовать существующую сборку ImageMagick и persistent batch, без четырёх
отдельных реализаций очереди. Для персонажа предусмотреть 1–3 референса.

В неизменный spec батча должны входить workflow, параметры адаптера, SHA-256
входных байтов и порядок кадров. Сначала сохранить байты референсов в SQLite,
затем загрузить их в ComfyUI; повторный status не загружает и не генерирует заново.
Неопределённый POST не повторять, чужой workflow через status не принимать,
не собирать частичный лист. Недостающие ноды/веса выявлять до отправки GPU jobs.
Оригинальный `drawer_anima` и его батчи должны продолжить работать без изменений.
