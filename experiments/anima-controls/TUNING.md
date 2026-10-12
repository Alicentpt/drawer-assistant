# All In One: ручная настройка

Открыть в ComfyUI: Workflows → Anima-Controls → all-in-one-v2-pose-depth.ui.
Если файла нет в списке, нажать «Обновить» в панели Workflows.
Это новая вкладка; старый workflow остаётся доступен.

Четыре цветные группы: 01 STYLE, 02 CHARACTER, 03 POSE, 04 DEPTH.
В каждой LoadImage выбирает её референс, ON / OFF включает влияние ветки.
Depth выключен по умолчанию. Для чистого текста выключить все четыре.
Даже выключенные LoadImage должны указывать на существующие файлы — ограничение
валидации ComfyUI. В тестовом поде заглушки уже загружены.
Стартовый размер нового AIO — 512×512. В чистом процессе на 3090 все четыре
ветки показали 8834 MiB (8.63 GiB); на 512×640 — 9244 MiB (9.03 GiB).
Это выборочные пики всей карты, не гарантия расхода на 3080.
После переключений ранее загруженные модели могут оставаться в GPU-кеше:
OFF отключает влияние ветки, но не обещает немедленного освобождения всей VRAM.

## С чего начать

1. KSampler: seed оставить фиксированным (`control_after_generate = fixed`).
   Сравнивать одну сцену на одном seed; менять один параметр за прогон.
2. Включить только POSE. Проверить скелет в Anima Pose Control; неверный скелет
   не исправить увеличением strength. Начать с R0_thin и strength 1.0.
3. Добавить CHARACTER, strength 1.0. Описать в positive волосы, глаза, одежду.
   Не описывать старую позу персонажа из reference, если требуется другая.
4. Добавить DEPTH при необходимости объёма/ракурса/расположения объектов.
   Начать со strength 0.4–0.6, end_percent 0.7–0.85; это наши ориентиры.
   Если поза и глубина с разных картинок противоречат друг другу, сначала
   ослабить Depth или выключить его и проверить каждую ветку отдельно.
5. Добавить STYLE последним, strength 0.4–0.8. Это экспериментальный IP-Adapter
   Character Reference; он может переносить внешность/цвет волос вместе со стилем.

## Что крутить

| Узел | Поле | Старт / следующий шаг |
|---|---|---|
| 01 STYLE — strength | strength | 0.4–0.8; прибавлять по 0.1; при смене внешности уменьшать |
| 02 CHARACTER — strength and sampling window | strength | 1.0; при дрейфе лица пробовать 1.2, затем 1.4 |
| 02 CHARACTER — strength and sampling window | end_percent | 1.0; при копировании старой композиции пробовать 0.8 |
| 03 POSE — strength | strength | 1.0; если скелет правильный, но игнорируется, пробовать 1.1–1.2 |
| 04 DEPTH — strength and sampling window | strength | 0.4–0.6 вместе с Pose; 0.8 отдельно |
| 04 DEPTH — strength and sampling window | end_percent | 0.7–0.85 освобождает последние шаги для деталей; 1.0 — весь проход |
| COMMON — seed / steps / CFG | steps / cfg | 30 / 4; не увеличивать всё одновременно |
| COMMON — AuraFlow shift | shift | 3 для прежнего набора; отдельно сравнить 5 с Depth на том же seed |

У Pose две ноды с весами: LoraLoaderModelOnly и AnimaControlApply.
LoRA strength_model оставить 1.0; силу позы менять в AnimaControlApply.
strength=0 не заменяет OFF: LoRA всё равно остаётся в цепочке.
У Character аналогично: основной регулятор — strength в AnimaInContextApply.
IP CFG separate оставить false; ip_cfg_scale — прежним. В предыдущем тесте
сильное независимое IP CFG испортило цвета и дало пересвет.

## Референсы и качество

Для лица полезен крупный план, для одежды — полный рост; автор Character
рекомендует два refs и маску фона. Текущий AIO имеет один character input,
так что второй ref требует AnimaRefEncode + AnimaRefLatentBatch, не коллажа.
Промпт должен согласовываться с желаемой позой и внешностью.

Pose Preview-2 лучше работает на 768/1024 по измерениям автора, но это повышает
расход VRAM. Для большего размера согласованно менять EmptyLatentImage,
AnimaRefEncode и ImageScale позы; не только один width/height.
Наш GPU-only бюджет проверять отдельно. Новые замеры ниже есть только для 3090;
старые 8.45 GiB относились к трём веткам без Depth.

Negative по прежнему требованию сохранён. В нём есть painting, cartoon, crayon,
graphite: они противодействуют живописному стилю. Для отдельного ручного опыта
можно убрать эти слова только в UI и сравнить на прежнем seed; рабочий пресет
бота от этого не изменится. Нельзя гарантировать «идеал» подбором одной силы.

## Источники

- [Pose: thin skeleton, strength и сравнительные разрешения](https://huggingface.co/Claquasse/Anima-Control-Pose).
- [Character: matching tags, два референса, strength, sampler](https://huggingface.co/darask0/Anima-InContext-Character).
- [Depth: DepthAnything V2 и flow shift 5](https://huggingface.co/TaihoC/Anima-ControlNet-VACE-Depth).
- [IP-Adapter: устройство и ограничения](https://github.com/LuciferTC9527/ComfyUI-Anima_IP-Adapter).

Диапазоны совместного Pose+Depth и порядок подбора выше — наши рабочие
предложения для экспериментов, а не опубликованные оптимальные параметры автора.
