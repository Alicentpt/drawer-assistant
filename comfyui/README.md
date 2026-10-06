# GPU для референсов Джессики

[Установка на Windows 11 / RTX 3080: Comfy Desktop, Tailscale, модели и ноды](WINDOWS.md).

Тест 06.10.2026: L40, ComfyUI `7ddf9a4f8aef66bca2eda1be2b936965be12b3b0`,
PyTorch 2.14.1+cu130. Штатные nodes, batch 1, 1024×1024, tiled VAE 512.
Каждый замер — новый процесс. `nvidia-smi` опрашивался примерно каждые 0,2 секунды:
это наблюдавшийся пик, не жёсткий лимит и не тест RTX 3080. Время включает загрузку
весов; один прогон не сравнивает скорость моделей строго.

| Модель и encoder | Шаги | Пик VRAM, МиБ | Пик, ГБ (10⁹) | Время, с |
|---|---:|---:|---:|---:|
| Z-Image W4A8 + Qwen W4A8 | 8 | 4661 | 4,89 | 25,37 |
| Z-Image W4A8 + Qwen abliterated FP8 | 8 | 5463 | 5,73 | 9,11 |
| Flux2 Klein mixed INT4/INT8 + Qwen W4A8 | 4 | 5285 | 5,54 | 15,29 |
| Flux2 Klein mixed INT4/INT8 + Qwen abliterated FP8 | 4 | 6427 | 6,74 | 16,84 |
| Krea2 Cat Tower INT8 + Qwen3-VL abliterated INT8 | 8 | 17061 | 17,89 | 66,41 |
| Krea2 Cat Tower INT4 + Qwen3-VL abliterated INT8 | 8 | 9950 | 10,43 | 30,66 |

Все шесть прогонов получили изображения. Krea2 не укладывается в бюджет 9 ГБ;
рабочий tool предлагает две компактные модели с abliterated encoder.
Их канонические графы — [в пакете плагина](../src/drawer_assistant/workflows/).
Остальные API JSON здесь — эксперименты, не выбор рабочего бота.

Запуск:

```bash
cd ~/ComfyUI
.venv/bin/python main.py --listen 127.0.0.1 --port 8188 --disable-auto-launch --gpu-only --disable-async-offload --cache-none --disable-cuda-malloc
```

CPU encoder, `--lowvram` и выгрузка моделей в RAM не используются. Обычная память
процесса/чтение файлов остаются: «без offload» не означает отсутствие системной RAM.
Для переключения моделей в одном процессе нужны отдельные замеры на целевой карте.

[models.json](models.json) фиксирует репозитории, revisions, размеры и SHA256
всех скачанных файлов; target — путь относительно `ComfyUI/models`. Хеши файлов
на сервере сверены. [benchmarks.json](benchmarks.json) — результаты замеров.
Веса и картинки в Git не входят. На предоставленном сервере уже всё скачано.

Присланный `nsfwUncensoredZImageTurbo_v10.zip` — архив на 4147 байт с workflow
`z_image_turbo_Low_vram.json`, без весов. Вместо него проверены настоящие компактные
веса [Z-Image W4A8](https://huggingface.co/CuTIsolation/Z-Image-Turbo-W4A8) и
[Flux2 Klein mixed INT4/INT8](https://huggingface.co/rockerBOO/flux2-klein-4b-nvfp4-convrot).
Несмотря на имя последнего репозитория, выбран файл mixed INT4/INT8, не NVFP4.
Z-Image/Flux используют Qwen3, Krea2 — отдельный Qwen3-VL, они не взаимозаменяемы.
Оригинальные encoder также скачаны; альтернативные графы сохранены.
