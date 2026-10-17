# Генерация на Windows 11 / RTX 3080 10 ГБ

Инструкция от 06.10.2026. Джессика, Telegram, заказы и база остаются на ноутбуке
разработчицы. На компьютере художницы работают Tailscale и ComfyUI.
Планируем до 9 ГБ VRAM, без CPU/RAM offload. Установка на RTX 3080 ещё не проверена:
[замеры L40](README.md) не доказывают совместимость и расход памяти на другой карте.

**1. Установить Comfy Desktop на компьютере с RTX 3080.**

1. Обновить [драйвер NVIDIA](https://www.nvidia.com/en-us/drivers/).
2. Скачать [Comfy Desktop для Windows](https://www.comfy.org/download), установить.
3. На главном экране выбрать **New Instance**, локальную установку, назвать
   `Jessica`, выбрать NVIDIA, если мастер спрашивает тип GPU. Дождаться установки.
   Python и зависимости устанавливает Desktop. Заложить примерно 50 ГБ свободного
   SSD: это наш запас для программы, моделей и результатов, не минимум ComfyUI.
4. Открыть карточку установки → **⋮ → Manage → Startup Args**. В Startup Arguments
   оставить менеджер и задать параметры ниже; убрать конфликтующие параметры
   памяти, если они уже есть. Затем перезапустить эту установку.

```text
--enable-manager --port 8188 --gpu-only --disable-async-offload --cache-none --disable-cuda-malloc
```

Адрес прослушивания оставить локальным, `127.0.0.1`. Проверка в браузере этого ПК:
`http://127.0.0.1:8188`. Если порт занят, закрыть другой ComfyUI, сохранить 8188.
Manager уже встроен; отдельно клонировать его не требуется.
[Установка](https://docs.comfy.org/installation/desktop/windows),
[настройки запуска](https://docs.comfy.org/installation/desktop/usage/manage),
[встроенный Manager](https://docs.comfy.org/manager/install).

**2. Отключить автоматический переход NVIDIA в системную память.**

При работающем ComfyUI найти его `python.exe` в Диспетчере задач → Подробности →
Открыть расположение файла. В **Панель управления NVIDIA → Управление параметрами
3D → Программные настройки** добавить именно этот Python. Параметр
**CUDA – Sysmem Fallback Policy → Prefer No Sysmem Fallback**, применить,
перезапустить ComfyUI. [Инструкция NVIDIA](https://nvidia.custhelp.com/app/answers/detail/a_id/5490).

`--gpu-only` запрещает offload моделей в ComfyUI; настройка драйвера отключает
другой механизм перехода в RAM. При нехватке VRAM уменьшаем разрешение/область
правки, не включаем `--lowvram`, CPU encoder или CPU VAE. Обычная RAM для Windows,
чтения файлов и процесса всё равно нужна. Параметры не устанавливают жёсткий
лимит 9 ГБ: фактический пик необходимо измерить.

**3. Поставить четыре полезных дополнения.**

В открытом ComfyUI: **Manager → Custom Nodes**, найти название, Install.
После установки перезапустить ComfyUI. Работать с менеджером локально на ПК.

| Искать в Manager | Для чего |
|---|---|
| [Advanced Model Manager — BISAM20](https://github.com/BISAM20/ComfyUI-advanced-model-manager) | Загрузка моделей по ссылкам Hugging Face и выбор папки |
| [ComfyUI's ControlNet Auxiliary Preprocessors](https://github.com/Fannovel16/comfyui_controlnet_aux) | Получение карты позы, глубины, контуров из референса |
| [ComfyUI-Inpaint-CropAndStitch](https://github.com/lquesada/ComfyUI-Inpaint-CropAndStitch) | Обработка небольшого участка по маске и вклейка обратно; также расширение холста |
| [rgthree-comfy](https://github.com/rgthree/rgthree-comfy) | Сравнение изображений, удобные переключатели и организация графа; необязателен для API |

Advanced Model Manager — удобство, а не обязательная зависимость генератора.
Если пакета нет в каталоге установленной версии Manager, файлы из следующего
шага можно скачать браузером и положить вручную. Встроенный редактор маски уже
есть: у `Load Image` открыть контекстное меню → **Open in MaskEditor**.

**4. Скачать модели Z-Image Turbo.**

Берём два пресета: обычный и abliterated. В обоих одна компактная модель Z-Image;
меняется **Qwen3 text encoder**, который в интерфейсе загружает нода `Load CLIP`.
Это не два разных diffusion checkpoint. Обычный encoder здесь W4A8, изменённый
FP8: их сравнение показывает разницу пресетов, не изолированный эффект ablation.
[W4A8](https://huggingface.co/CuTIsolation/Z-Image-Turbo-W4A8),
[abliterated encoder](https://huggingface.co/BoyoDiffusion/qwen_abliterated_fp8_e4m3fn.safetensors).

Папки в таблице относительны каталогу **models** вашей установки/общей библиотеки
Desktop. Её расположение смотрите в **Manage → Storage**. При ручном скачивании
открывайте этот каталог, а не папку программы в Program Files.

| Скачать файл | Размер на диске, ГБ | Подпапка в models |
|---|---:|---|
| [z_image_turbo_w4a8.safetensors](https://huggingface.co/CuTIsolation/Z-Image-Turbo-W4A8/resolve/2213223dca1a34dd32412f13a004224c51a97050/z_image_turbo_w4a8.safetensors) | 3,49 | `diffusion_models` |
| [qwen_3_4b_w4a8.safetensors](https://huggingface.co/CuTIsolation/Z-Image-Turbo-W4A8/resolve/2213223dca1a34dd32412f13a004224c51a97050/qwen_3_4b_w4a8.safetensors) | 2,83 | `text_encoders` |
| [qwen_abliterated_fp8_e4m3fn.safetensors](https://huggingface.co/BoyoDiffusion/qwen_abliterated_fp8_e4m3fn.safetensors/resolve/a92b65b37e02c52342bde6c15997f8ee2c766010/qwen_abliterated_fp8_e4m3fn.safetensors) | 4,02 | `text_encoders` |
| [ae.safetensors](https://huggingface.co/Comfy-Org/z_image_turbo/resolve/6fc90a3b1b653e935a0d175e260736de25b84df5/split_files/vae/ae.safetensors) | 0,34 | `vae` |
| [Z-Image-Turbo-Fun-Controlnet-Union-2.1-lite-2602-8steps.safetensors](https://huggingface.co/alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union-2.1/resolve/5155fc56d17821007d6f62ac192c09e0f0e72016/Z-Image-Turbo-Fun-Controlnet-Union-2.1-lite-2602-8steps.safetensors) | 2,02 | `model_patches` |
| [Z-Image-Turbo-Fun-Controlnet-Tile-2.1-lite-2601-8steps.safetensors](https://huggingface.co/alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union-2.1/resolve/5155fc56d17821007d6f62ac192c09e0f0e72016/Z-Image-Turbo-Fun-Controlnet-Tile-2.1-lite-2601-8steps.safetensors) | 2,02 | `model_patches` |

Первые четыре — базовая генерация, примерно 10,7 ГБ файлов. Все шесть — 14,7 ГБ.
Последние два — для следующего этапа правок, на нашей связке ещё не испытаны.
Размеры файлов не равны расходу VRAM; два encoder одновременно не загружаем.

В Advanced Model Manager: **Ctrl+Shift+M → Paste Link → вставить ссылку → Resolve →
проверить папку → Download**. Повторить для файлов, перезапустить ComfyUI.
Если `model_patches` не предлагается, создать её в models и скачать туда браузером.
Не скачивать весь репозиторий и не нажимать загрузку всех моделей из чужого
шаблона: он может предложить полноразмерные BF16-веса вместо наших компактных.

Union Lite объединяет Canny, HED, Depth, Pose, MLSD, Scribble и Gray; поддерживает
работу с маской. Tile Lite предназначен для увеличения/детализации изображения.
Это локальные веса Alibaba-PAI, API-ключ Alibaba им не нужен.
[Карточка набора](https://huggingface.co/alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union-2.1).
Для нативного ComfyUI это именно `model_patches`, как в
[официальном workflow](https://docs.comfy.org/tutorials/image/z-image/z-image-turbo).

Препроцессоры скачивают свои дополнительные веса при использовании. Начинаем
с Canny или готовой карты позы. DWPose и Depth Anything V2 включаем отдельно,
проверив выполнение нейросетей на CUDA: сам `--gpu-only` не гарантирует, что
сторонняя нода не выбрала CPU. В Crop and Stitch выбираем GPU device mode.

**5. Проверить генерацию локально.**

Для обычного пресета есть [наш API JSON](zimage-w4a8.api.json), для abliterated —
[граф рабочего инструмента](../src/drawer_assistant/workflows/zimage-w4a8-abliterated.api.json).
Это формат для API; для ручной работы можно взять Z-Image Turbo из Templates и
выставить имена файлов из таблицы. Встроенная поддержка W4A8 требует свежего core;
при неизвестном формате сначала обновить движок через Manage → Update.

Начальные настройки: batch 1, 768×768, 8 шагов, CFG 1, `res_multistep` + `simple`,
`ModelSamplingAuraFlow` shift 3, `VAE Decode (Tiled)` tile 512 / overlap 64.
У `Load CLIP` type `lumina2`, device `default`; при `--gpu-only` это GPU.
Сделать по картинке с каждым encoder, затем повторить в 1024×1024.
Для чистоты замера первый запуск каждого пресета — после перезапуска ComfyUI.
После этого отдельно проверить переключение пресетов в одном процессе.

На L40 базовые пресеты дали наблюдавшиеся пики 4,89 и 5,73 ГБ при 1024×1024.
На 3080 проверяем качество, CUDA-совместимость и пик заново. Для наблюдения:

```powershell
nvidia-smi --query-gpu=memory.used,memory.total --format=csv -lms 200
```

Остановить Ctrl+C. Единицы — MiB; 9 десятичных ГБ ≈ 8583 MiB. Это выборочные
замеры всей карты, включая другие программы, а не гарантия отсутствия короткого
пика. Проверить также dedicated/shared GPU memory в Диспетчере задач и логи Comfy.
Для ControlNet начать с одной карты управления и 768×768, для inpaint — с crop
512–768. Если не помещается, этот workflow не принимаем в GPU-only набор.

**6. Установить Tailscale на обоих компьютерах.**

1. Скачать [Tailscale для Windows](https://tailscale.com/download/windows), установить.
2. В значке возле часов выбрать Log in. Каждая входит своим Google/Microsoft
   аккаунтом. Отдельного общего пароля Tailscale здесь нет; ключами и шифрованием
   управляет программа. [Вход на Windows](https://tailscale.com/docs/install/windows).
3. Художница открывает [Machines](https://login.tailscale.com/admin/machines),
   у своего компьютера выбирает **Share**, создаёт приглашение и передаёт ссылку
   разработчице. Та принимает его под своим аккаунтом. Это доступ к выбранному
   компьютеру. [Sharing](https://tailscale.com/docs/features/sharing).
4. На компьютере художницы открыть новый PowerShell и выполнить:

```powershell
tailscale serve --bg http://127.0.0.1:8188
tailscale serve status
```

Если первая команда предложит включить HTTPS, открыть выданную ей ссылку и
подтвердить, затем повторить команду. Скопировать HTTPS-адрес из вывода, например
`https://artist-pc.example.ts.net`. Открыть его на ноутбуке с включённым Tailscale.
Нужен полный адрес с `.ts.net`, не только короткое имя компьютера.
[Serve](https://tailscale.com/docs/reference/tailscale-cli/serve).

Это приватный доступ через Tailscale; Funnel и открытие 8188 на роутере не нужны.
Динамический IP подходит. При невозможности прямой связи Tailscale использует
relay через исходящий TCP 443; сеть всё же должна разрешать доступ к Tailscale.
[Порты и relay](https://tailscale.com/docs/reference/faq/firewall-ports).

**7. Подключить к Джессике — на ноутбуке разработчицы.**

После локальной проверки заменить в `.env` репозитория адрес на реальный из Serve:

```dotenv
COMFYUI_BASE_URL=https://artist-pc.example.ts.net
```

В каталоге drawer-assistant:

```powershell
uv run drawer-setup
hermes.cmd --profile default gateway restart --all
```

SSH-туннель для этого подключения не нужен. Если на ноутбуке есть HTTP-прокси,
внести полный `.ts.net` hostname в исключения прокси/NO_PROXY окружения gateway.
Одна запись NO_PROXY в `.env` проекта не меняет окружение уже запущенного gateway.
Проверить `/system_stats` через приватный адрес и один вызов инструмента из группы.
Сверить модель, картинку и время генерации. Установить запрет сна на время теста;
для генерации оба ПК должны быть включены, ComfyUI и Tailscale — запущены.

**8. Какие workflows собираем после установки.**

| Сценарий | Вход | Основа |
|---|---|---|
| Новая иллюстрация | Prompt, seed, размер | Два пресета Z-Image с разными encoder |
| Вариант наброска | Изображение, prompt, сила изменения | Img2img через VAE |
| Поза / композиция / контуры | Изображение или готовая карта управления, prompt | Union Lite + нужный препроцессор |
| Исправить участок | Изображение, маска, prompt | Union Lite inpaint + Crop and Stitch |
| Расширить холст | Изображение, границы расширения, prompt | Маска новых областей + inpaint |
| Добавить детализацию | Изображение, масштаб | Tile Lite, обработка частями |

ControlNet переносит геометрию/структуру; узнаваемость лица или персонажа отдельно
проверяем по референсам. Установка нод ещё не добавляет эти способности боту.
Сейчас рабочий Z-Image tool принимает текст и использует abliterated encoder;
обычный пресет есть в экспериментальном JSON. Следующим изменением нужны отдельный
tool обычного пресета, загрузка изображений/масок через API ComfyUI и проверенные
API-графы правок. Передаваемые референсы берём из BLOB базы заказов.

Приёмка каждого нового графа: реальная 3080, без offload и до 9 ГБ, сохранённый
результат, корректная маска/геометрия, время выполнения, повторный запуск и смена
пресета. После успешного теста фиксируем версии core/nodes, хеши весов и snapshot
установки. Если ПК художницы выключен, заказы и напоминания продолжают работать
на ноутбуке; автоматическое ожидание включения GPU пока не реализовано.
