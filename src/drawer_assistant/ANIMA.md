# Anima / Nova Anime AM v2.0

Инструмент: `drawer_anima`. Пресет: Qwen3 0.6B Base, 512×512,
Euler ancestral / simple, 25 шагов, CFG 5, denoise 1, batch 1, GPU-only.
Это выбранный пресет Nova; настройки Anima Turbo к нему не применяются.

## Обязательные дополнения пользователя

Код сам добавляет positive prefix один раз; передавай в `prompt` только сцену.
Готовый текст пользователя сохраняй дословно. Не вставляй метки `Prompt:` или
`Negative Prompts:` в описание сцены. Этот согласованный пресет — исключение
из правила передачи промпта без дополнений; другие модели он не затрагивает.

Positive: `masterpiece, best quality, score_9, score_8, score_7, year 2025, newest, highres, absurdres, very aesthetic, scenery, {Prompt}`

Negative: `worst quality, low quality, early, old, score_1, score_2, score_3, cartoon, graphic, painting, crayon, graphite, abstract, glitch, deformed, mutated, ugly, disfigured, long body, bad anatomy, bad hands, missing fingers, extra fingers, extra digits, fewer digits, cropped, very displeasing, artist name, blurry, jpeg artifacts, lowres, censor`

Это пользовательский пресет, не универсальная рекомендация авторов.
`scenery` усиливает окружение; negative `painting`, `cartoon` может мешать запросу
именно на эти стили. При таком конфликте объясни его, не меняй пресет молча.

## Когда пользователь просит составить промпт

- Пиши теги строчными буквами, с пробелами вместо `_`; исключение — `score_7` и т.п.
- После автоматически добавленных quality/year/meta тегов: число персонажей,
  персонажи и серия, затем стиль и прочие признаки. Для двух женщин — `2girls`.
- Смешивай теги с английским описанием минимум из двух предложений: действие,
  расположение, внешность, одежда, окружение, свет и настроение.
- Каждому персонажу дай отдельное описание внешности и действия; обозначь слева/
  справа. Одних имён недостаточно. Имена и названия в предложениях пиши обычно.
- По просьбе использовать artist tag ставь `@` перед именем. Усиление `(tag:2)`
  поддерживается; не добавляй веса повсеместно. Длинный текст внутри картинки
  модель воспроизводит ненадёжно.

Пример сцены: `2girls, witch hats, pumpkins, autumn leaves. Two adult witches
decorate a moonlit greenhouse. On the left, a red-haired woman in a green coat
holds a carved pumpkin; on the right, a silver-haired woman in a violet dress
untangles a string of tiny ghost lanterns. They exchange amused glances as a
black cat steals a ribbon. Warm candlelight contrasts with blue moonlight.`

Вызови `start`, затем `status` с тем же id; отправь `MEDIA:` и `generation_seconds`.
При ошибке не обещай картинку и не меняй провайдера. Референсы/img2img есть только
в отдельном UI workflow; этот инструмент принимает текст.

Источники, проверены 07.10.2026:
[авторы Anima: Prompting](https://huggingface.co/circlestone-labs/Anima#prompting),
[ComfyUI: установка и workflow](https://docs.comfy.org/tutorials/image/anima/anima).
