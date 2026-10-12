# Четыре экспериментальных workflow Anima

Статус: API-графы собраны по исходникам нод, но на RTX 3080 ещё не испытаны.
Рабочие инструменты Hermes пока используют прежний text-to-image. Эти файлы
не входят в allowlist генератора и не подменяют действующий workflow.

Все четыре используют нашу Nova Anime AM v2.0, Qwen3 0.6B Base и Qwen Image VAE.
Размер 512×512, один кадр на GPU job. Quality prefix и negative сохранены.
Совместимость адаптеров с Nova, качество и предел 9 ГБ VRAM требуют живого теста.

| API-граф | Вход | Дополнительные ноды и веса |
|---|---|---|
| `style.api.json` | образец стиля | Anima IP-Adapter, SigLIP2, `ip_adapter-Character_Reference-10.safetensors` |
| `character.api.json` | персонаж | Anima InContext, `anima-incontext-character.safetensors` |
| `depth.api.json` | фото/рисунок композиции | DepthAnythingV2 small, Advanced ControlNet с поддержкой Anima VACE, `anima-vace-depth.safetensors` |
| `pose.api.json` | фото/рисунок позы | AnimaPoseControl, AnimaControlApply, `anima_pose_preview2.safetensors`, два ONNX детектора |

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

Зависимости каждой ноды устанавливаются Python-интерпретатором именно ComfyUI.
Веса не загружаются на ноутбук агента. Перезапуск ComfyUI — после установки
зависимостей и GPU-only правок; затем проверить `/object_info` и `/system_stats`.

## GPU-only

ComfyUI должен работать с `--gpu-only`. Одного флага для сторонних нод недостаточно:

- IP-Adapter вызывает `siglip_encoder.to("cpu")`. `style-gpu-only.patch` убирает
  эти три выгрузки в закреплённом `nodes.py`; SigLIP остаётся в VRAM.
- Pose изначально создаёт ONNX детектор на CPU. `pose-gpu-only.patch` меняет
  закреплённый `ComfyUI-anima-pose-control/detect.py` на CUDA и запрещает запуск
  без `CUDAExecutionProvider`. Нужен совместимый `onnxruntime-gpu`, а не CPU пакет.

До применения сверить SHA-256 исходного файла из `revisions.json`, затем проверить
патч через `git apply --check` из каталога соответствующей ноды. Не применять
к другой версии вслепую. Эти патчи подготовлены локально, на сервере не применены.
Они ещё не доказывают отсутствие других выгрузок: на испытании проверяем устройства
моделей и пиковую VRAM. При OOM уменьшаем размер/меняем адаптер, без CPU fallback.

## Референсы и проверка

Для предметной проверки нужны отдельные изображения:

1. `character.png`: один персонаж, хорошо видны лицо и одежда; желательно полный
   рост. Дополнительный крупный план лица полезен для следующего теста с двумя refs.
2. `style.png`: характерная линия, палитра, свет и фактура; желательно другая сцена
   и другой персонаж, чтобы отличить перенос стиля от копирования содержания.
3. `composition.png`: нужная поза и ракурс, тело не обрезано и не перекрыто.
   Один файл подходит для тестов Pose и Depth; для Depth полезен читаемый фон.

Через ComfyUI загрузить их как `jessica-tests/<имя>.png`; в API-графах это пути
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
