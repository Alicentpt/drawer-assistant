---
name: drawer-anima
description: "Промптинг Nova: сцена, художественный стиль, изменяемые positive/negative и параметры генерации."
---

# Anima / Nova Anime AM v2.0

Только novaAnimeAM_v20.safetensors + Qwen3 0.6B Base + Qwen Image VAE.
Diffusion Anima Base не подставлять. Инструменты — MCP drawer_generation.
Процедура вызова: drawer-images; референсы: drawer-anima-controls.

Старт: 512×512, CFG 7.0, 45 steps, res_multistep/simple, denoise 1.
Размеры кратны 64; пределы схемы не гарантируют вместимость в VRAM.
RTX 3080 10 ГБ, целевой бюджет 9 ГБ, GPU-only без CPU/RAM offload.
При OOM сообщи сбой, предложи меньший размер/число refs или явный повтор.
Не включай offload и не меняй модель.

## Изменяемый preset

Код добавляет positive_prefix один раз; prompt — только сцена, без меток
Prompt/Negative Prompts и без повторения всего пресета.

Positive по умолчанию:
`masterpiece, painting, best quality, score_9, score_8, score_7, year 2025, newest, highres, absurdres, very aesthetic, scenery, `

Negative по умолчанию:
`worst quality, low quality, early, score_1, score_2, score_3, graphic, crayon, graphite, abstract, glitch, deformed, mutated, ugly, disfigured, long body, bad anatomy, bad hands, missing fingers, extra fingers, extra digits, fewer digits, cropped, very displeasing, artist name, blurry, jpeg artifacts, lowres, censor`

Это стартовый пользовательский preset, а не неотменяемое ограничение.
Проверяй конфликт с замыслом: painting мешает фотографии, graphite/crayon
в negative мешают карандашной работе. Ты МОЖЕШЬ передать
options.positive_prefix и options.negative_prompt: каждое полностью заменяет
соответствующий список; пустая строка отключает. Для удаления одного тега
передай весь оставшийся список. В batch — defaults или options конкретного кадра.
Сохраняй остальные полезные quality-теги и сюжет; существенную правку объясни кратко.
Готовый промпт пользователя не переписывай без просьбы. Не добавляй автоматически
ограничения одежды или содержания, меняющие идею.

## Промпт

- Добавляй английское описание манеры: anime illustration, oil painting,
  digital painting, pencil drawing, watercolor и т.п. Дополняй фактурой,
  мазками, обработкой краёв, палитрой и светом; без противоречивой мешанины.
- Теги строчными буквами, с пробелами вместо подчёркиваний; score_9 и т.п.
  сохраняются. Artist tag — @ перед именем.
- Сначала число героев, персонаж/серия при необходимости, внешность и действие,
  затем композиция и стиль. Короткие теги сочетай с ясным английским описанием.
- Один видимый момент: кто, что делает, где, куда смотрит. Дедлайн, цену
  и переписку заказа не включай. Согласуй ракурс/крупность: не close-up + full body.
- Нескольких героев описывай отдельно, закрепляй слева/справа; не смешивай волосы
  и одежду в общий неоднозначный список. Взаимодействия задавай явно.
- Для Style дополнительно опиши манеру словами. Для внешности используй текст:
  отдельного Character/InContext инструмента больше нет.
- Для вариантов/панелей anima_batch, отдельный prompt на кадр. Подписи рисует
  сборщик. Точность лица, анатомии, количества объектов и текста не гарантирована.

Источники: [Anima Prompting](https://huggingface.co/circlestone-labs/Anima#prompting),
[ComfyUI](https://docs.comfy.org/tutorials/image/anima/anima).
Наши параметры Nova — пользовательский preset, не универсальный рецепт авторов.
