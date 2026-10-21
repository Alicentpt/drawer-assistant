# Перенос Джессики на Linux

**Цель:** Hermes в Docker Compose на EC2 → управляемый PostgreSQL.
Генерация остаётся на ПК художницы через Tailscale. В production SSH-туннель к БД
не нужен. PostgreSQL не запускаем вторым сервисом Compose.

## Что где хранится

| Данные | Место |
| --- | --- |
| Заказы, вложенные этапы, напоминания, задания генерации | PostgreSQL |
| Бинарные референсы, SHA-256, связи с заказами | PostgreSQL, `BYTEA` |
| SOUL, навыки, память, история и cron самого Hermes | Постоянная папка `state/hermes` |
| Экспорты референсов и сгенерированные файлы | Локальная папка `DRAWER_DATA_DIR` внутри volume |
| Токены, OAuth, URL БД | Закрытые файлы профиля; отдельно от Git и образа |

Миграция прикладной БД **не переводит внутреннюю историю Hermes в PostgreSQL**.
Её SQLite-файлы переносятся вместе с остановленным профилем.

## Выбор управляемой базы

- **RDS PostgreSQL:** Single-AZ, `db.t4g.micro`, gp3 20 GiB, PostgreSQL 17.
  В том же регионе и VPC, что EC2. Public access — No; вход TCP 5432 только
  от security group EC2. Без RDS Proxy и Insights Advanced.
- **Neon:** обычный PostgreSQL, direct endpoint; TLS с проверкой сертификата.
  Учесть лимиты compute, хранилища и восстановления выбранного тарифа.

Оценка на 16.10.2026 для RDS в `eu-north-1`: $0.016/час × 730 + $0.12/GiB × 20
= **$14.08/месяц** до налогов и доплат. Бюджет $11 не покрывает этот вариант.
Neon Free: $0, 1 GB базы, 100 CU-часов/месяц; изображения расходуют место в базе.
Цены и доступные версии проверять перед созданием:
[AWS](https://aws.amazon.com/rds/postgresql/pricing/),
[Neon](https://neon.com/pricing).

## Подготовка контейнера

На сервере с Docker Compose, из корня репозитория:

```bash
sudo docker compose -f deployment/compose.yaml build
sudo docker compose -f deployment/compose.yaml config --quiet
```

Образ закрепляет официальный Hermes по digest, добавляет драйвер PostgreSQL,
наш пакет и ImageMagick 7. Python-пакеты плагина лежат отдельно в
`/opt/drawer-python`, подключённом через `.pth`, в том числе для subprocess tools.
Закреплённые пакеты Hermes не обновляются. Образ сохраняет штатный
entrypoint и supervision Hermes.
Секреты и пользовательские файлы исключены из build context.

`state/hermes` монтируется в `/opt/data`. UID/GID по умолчанию 1000/1000;
при необходимости задать `HERMES_UID`/`HERMES_GID`. Лимит памяти 768 MiB
настраивается через `HERMES_MEMORY_LIMIT`. На EC2 заранее проверить свободную
память: существующие сервисы не останавливать ради запуска бота.

Сеть контейнера — отдельный Docker bridge без опубликованных портов, чтобы
соседний Hermes на EC2 имел собственные порты и управляющие сокеты.
Исходящий трафик использует маршруты хоста; на EC2 должен работать Tailscale
в нужной сети. DNS контейнера — Tailscale `100.100.100.100`: это разрешает имена
`*.ts.net`, сохраняя DNS хоста. Проверить RDS и HTTPS ComfyUI `system_stats`
из контейнера.

## Подготовка профиля

1. Сохранить папку профиля и внешнюю `DRAWER_DATA_DIR`. Для окончательного переноса
   сначала остановить gateway на ноутбуке, чтобы SQLite/WAL не менялись при копии.
2. Передать профиль и секреты на свой EC2 по SSH в закрытые папки. Не передавать
   токены через чат, Git, Docker build arguments или команды, печатающие `.env`.
   Обычный `hermes profile export` исключает credentials: одного экспорта мало.
3. Создать структуру `/opt/data/profiles/drawer-assistant` в смонтированной папке,
   сохранить пользовательские SOUL, память, skills и OAuth. Не переносить
   посторонние профили и Windows `.venv`.
4. В приватном файле настроек для `drawer-setup` указать Linux-пути:

   ```dotenv
   HERMES_SOURCE_DIR=/opt/hermes
   DRAWER_DATA_DIR=/opt/data/profiles/drawer-assistant/drawer-data
   DRAWER_TIMEZONE=Europe/Moscow
   COMFYUI_BASE_URL=https://ARTIST_PC.TAILNET.ts.net
   COMFYUI_ANIMA_BASE_URL=
   DRAWER_DATABASE_URL=postgresql://USER:PASSWORD@HOST/DATABASE?sslmode=verify-full&sslrootcert=system
   ```

   Сохранить там также существующие токен, ID группы и настройки OpenRouter.
   Для RDS заменить CA на `sslrootcert=/etc/ssl/certs/rds-global-bundle.pem`.
   Спецсимволы пароля URL-кодировать. Файл и credentials — права 600, папки 700.

5. В одноразовом контейнере выполнить настройку, **не запуская gateway**:

   ```bash
   sudo docker compose -f deployment/compose.yaml run --rm --no-deps \
     --user 1000:1000 --entrypoint /opt/drawer-python/bin/drawer-setup \
     -e HERMES_HOME=/opt/data -e HOME=/opt/data \
     -e PATH=/opt/drawer-python/bin:/opt/hermes/.venv/bin:/usr/bin:/bin hermes \
     --profile-dir /opt/data/profiles/drawer-assistant \
     --env-file /opt/data/drawer.env
   ```

   Это отдельная CLI-операция без s6 и запуска poller. UID/GID должны совпадать
   с владельцем volume (по умолчанию 1000:1000). Обычный gateway запускается
   штатным entrypoint, без `--user` и без его переопределения.

   Настройщик сохраняет пользовательский SOUL, обновляет плагин, проверяет патч
   Telegram, пересоздаёт Linux-пути и обновляет два cron по имени: каждые 30 минут
   и 12:00 PM МСК. Проверить также `skills.external_dirs`, terminal cwd, сторонние
   MCP-команды и кешированные пути в истории заданий: `C:\...` на Linux не работает.
   Байты сохранённых референсов в БД от старого кеша не зависят.

## Перенос БД и переключение

**До переключения выбранная PostgreSQL должна быть пустой.** Наш CLI создаст
схему, сверит все девять таблиц и SHA-256 бинарников. Частичная копия откатывается;
отличающиеся существующие данные не перезаписываются.

Пример в Windows; реальные пути и URL оставить в приватных файлах:

```powershell
uv run drawer-migrate snapshot C:\path\drawer.sqlite3 --backup .local\cutover.sqlite3
uv run drawer-migrate copy .local\cutover.sqlite3 --env-file .env
uv run drawer-migrate verify .local\cutover.sqlite3 --env-file .env
```

Для приватной RDS команды copy/verify выполняются **на EC2**, где доступна БД.
Передать snapshot по SSH и запустить тот же CLI внутри одноразового контейнера.
Операции CLI не запускают бота. Повтор copy идентичных данных безопасен.
Окончательный snapshot делать после остановки записей; тестовый старый snapshot
не годится для переключения, если с тех пор появились изменения.

После проверки записать URL в `.env` профиля через `drawer-setup`. Только когда
gateway на ноутбуке остановлен, запустить серверный:

```bash
sudo docker compose -f deployment/compose.yaml up -d
sudo docker compose -f deployment/compose.yaml ps
sudo docker compose -f deployment/compose.yaml logs --tail 50
```

Проверить одну общую сессию группы, закрытую личку, заказы, экспорт бинарного
референса, оба расписания и доступ к Anima. Два poller одного токена не запускать.

**Откат до новых записей:** остановить EC2 gateway, вернуть локальный профиль
и SQLite snapshot, очистить DRAWER_DATABASE_URL профиля, запустить ноутбук.
После новых записей в PostgreSQL старый SQLite уже не актуален: автоматического
обратного переноса нет, сначала согласовать данные. Бэкапы профиля и БД хранить
отдельно от контейнера и проверять восстановление.

## Проверка разработки

```powershell
uv run poe check
uv run poe audit
uv run --env-file .local/postgres-test.env pytest tests/test_postgres.py
```

Последняя команда требует `DRAWER_TEST_DATABASE_URL` только тестового PostgreSQL:
тест создаёт уникальную схему и удаляет её. В CI выделен отдельный PostgreSQL 17.
Ручные SQL-операции на рабочей базе в тестах не выполнять.
