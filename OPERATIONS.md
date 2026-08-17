# Эксплуатация и восстановление

## Назначение

Этот runbook относится к production-развёртыванию личного paper-trading бота. Приложение не
должно получать ключи с правом реальной торговли. Production readiness требует одновременной
готовности PostgreSQL, обоих источников рынка, champion-модели и Telegram-бота.

## Обязательные переменные окружения

- `INCOME_TG_ENVIRONMENT=production`;
- `INCOME_TG_DATABASE_URL` — DSN приложения;
- `INCOME_TG_BOT_TOKEN`;
- необязательный `INCOME_TG_TELEGRAM_PROXY_URL` для доступа только к Telegram API;
- `INCOME_TG_TELEGRAM_OWNER_ID`;
- `INCOME_TG_PAPER_ONLY=true`;
- `INCOME_TG_DEBUG=false`;
- `INCOME_TG_MARKET_SOURCES=BYBIT,OKX`;
- `INCOME_TG_SYMBOLS=BTCUSDT,ETHUSDT,TONUSDT`;
- отдельная переменная, например `INCOME_TG_BACKUP_DATABASE_DSN`, с DSN для backup-задачи.

Значения токена и DSN нельзя передавать аргументами командной строки. Скрипты принимают только
имя переменной с DSN и не выводят её значение или сообщения внешнего процесса.

## Preflight

Перед первым запуском и после изменения production-конфигурации:

```bash
python /app/scripts/production_preflight.py \
  --backup-dir /var/backups/income-tg \
  --model-artifact /var/lib/income-tg/models/champion.json \
  --pg-dump /usr/bin/pg_dump \
  --backup-dsn-env INCOME_TG_BACKUP_DATABASE_DSN
```

Ненулевой код означает запрет запуска. Вывод содержит только коды проблем, без секретов.

## Health и readiness

`HealthAggregator` параллельно собирает четыре компонента: `DATABASE`, `MARKET`, `MODEL`, `BOT`.
Исключения probe преобразуются в безопасный код `PROBE_FAILED`, timeout — в `PROBE_TIMEOUT`.
Текст исключения не публикуется. `ReadinessPolicy` по умолчанию требует свежий статус `HEALTHY`
всех четырёх компонентов не старше 30 секунд.

Процесс приложения должен периодически сохранять итог readiness в локальный health endpoint или
snapshot-файл. Docker healthcheck должен проверять именно этот итог, а не только наличие процесса.

## Резервное копирование

Backup запускается ежедневно после завершения расчётного дня:

```bash
python /app/scripts/backup_postgres.py \
  --backup-dir /var/backups/income-tg \
  --pg-dump /usr/bin/pg_dump \
  --dsn-env INCOME_TG_BACKUP_DATABASE_DSN \
  --retention-days 14 \
  --minimum-to-keep 3
```

Скрипт создаёт custom-format dump и соседний SHA-256 файл. Retention только выводит явно
проверенный список кандидатов; автоматически файлы не удаляются. Удаление выполняется отдельной
операторской процедурой после проверки списка и наличия внешней копии.

Ежедневно проверяются: возраст последней копии не более 26 часов, совпадение SHA-256 и наличие
минимум трёх последних копий. Не реже раза в месяц выполняется тестовое восстановление в отдельную
пустую базу.

## Тестовое восстановление

1. Остановить тестовый bot/worker, не production-процессы.
2. Создать новую пустую PostgreSQL базу.
3. Проверить `.dump.sha256` через `verify_backup_artifact`.
4. Выполнить `pg_restore` только в новую тестовую базу.
5. Применить `alembic upgrade head`.
6. Проверить replay ledger, число событий и отсутствие отрицательных остатков.
7. Проверить champion pointer и SHA-256 артефакта модели.
8. Проверить market и bot checkpoints.
9. Запустить `check_recovery_state`; все issue должны отсутствовать.
10. Удалять тестовую базу только отдельной явно подтверждённой операцией.

## Восстановление после остановки

Порядок запуска: PostgreSQL → миграции → market collector → model worker → bot. До готовности всех
компонентов новые сигналы блокируются. После запуска нужно убедиться, что backfill закрыл разрыв
свечей, стакан получил новый snapshot, активна проверенная champion-модель, bot checkpoint не
откатился, а replay финансового журнала совпадает со snapshot.

Если recovery check сообщает `LEDGER_INCONSISTENT`, `BACKUP_UNVERIFIED` или
`MODEL_ARTIFACT_MISSING`, paper trading и публикация сигналов остаются остановленными до ручного
разбора. Исходные записи ledger не исправляются на месте.

## Требуемые изменения Docker Compose

- копировать каталог `scripts` и `OPERATIONS.md` в runtime image;
- установить PostgreSQL client той же major-версии, что и сервер, чтобы был доступен `pg_dump`;
- подключить именованные volumes для `/var/backups/income-tg` и `/var/lib/income-tg/models`;
- передавать backup DSN через Docker secret или закрытый env-файл, не через YAML;
- добавить отдельный однократный `preflight` service и ежедневный backup scheduler;
- healthcheck bot/worker направить на агрегированный readiness;
- сохранить `restart: unless-stopped`, настроить лимиты логов и внешний мониторинг;
- не публиковать PostgreSQL port наружу production-хоста.
