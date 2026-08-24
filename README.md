# Income TG

Личный русскоязычный Telegram-бот для анализа крипторынка, объяснимых торговых сигналов и paper trading. Реализованы инкременты 0–8: сбор данных Bybit/OKX, USDT/RUB, риск-контур, симулятор, backtest, ML, сигналы, автоматическое переобучение и production-наблюдаемость.

Система намеренно работает только в paper-режиме и не имеет ключей для реальной торговли. Прибыль не гарантируется: допуск к реальным деньгам возможен только после отдельной проверки результатов длительного paper-периода.

## Что работает

- доступ только для одного Telegram ID;
- постоянная клавиатура, интерактивная справка и копирование примеров команд;
- реальный портфель `Crypto Wallet`;
- отдельный виртуальный портфель со стартовым балансом 100 000 RUB;
- пополнение, вывод, покупка и продажа;
- полная ручная сверка остатков;
- защита от повторной записи одного Telegram update;
- неизменяемый ledger на уровне PostgreSQL;
- отображение RUB и USDT при заданном ручном курсе;
- Docker Compose, миграции и тесты.
- круглосуточные Bybit/OKX collectors со свечами, сделками, стаканом и derivatives;
- проверка свежести, sequence gaps, резервной цены и периодический backfill;
- признаки и горизонты `5m`, `15m`, `1h`, `4h` без look-ahead;
- champion/challenger ML с классами `SHORT` / `NO TRADE` / `LONG`, калибровка,
  адаптивный порог активности, weekly retraining и rollback;
- сигналы `LONG`, `SHORT`, `CLOSE`, `HOLD` с уверенностью и объяснением;
- динамический размер позиции, стоп, единственный тейк, плечо до 20x и лимиты риска;
- атомарный paper ledger, auto-exit, комиссии, spread, slippage, funding и ликвидация;
- event-driven backtest, walk-forward и baseline-стратегии;
- transactional outbox, история сигналов/сделок, readiness, backup и recovery runbook.

## Требования

- Linux VPS или локальный компьютер с Docker Engine и Compose plugin;
- Git для получения и обновления проекта;
- Python 3.12 только для запуска без Docker и разработки;
- Telegram bot token от BotFather;
- Telegram ID владельца.

Серверу не нужны входящие порты приложения: Telegram работает через long polling, а PostgreSQL опубликован только на `127.0.0.1`. Нужен исходящий доступ к Telegram, GitHub, Bybit, OKX и CoinGecko.

## Как устроено приложение

```text
Bybit + OKX + USDT/RUB
          ↓
проверка и хранение рыночных данных
          ↓
признаки → champion-модель → сигнал
          ↓
risk engine → paper execution → ledger
          ↓
Telegram-уведомления, статистика и история
```

Реальные ордера не отправляются. Worker исполняет рекомендации в изолированном paper-портфеле, а ручные команды кошелька записываются отдельно в `Crypto Wallet`.

## Пошаговый запуск на Linux/VPS

### 1. Получить проект

```bash
git clone https://github.com/InferKing/income-bot.git
cd income-bot
cp .env.example .env
```

### 2. Создать Telegram-бота

1. Открыть [@BotFather](https://t.me/BotFather).
2. Выполнить `/newbot` и сохранить выданный токен.
3. В `.env` временно указать токен и любой положительный owner ID:

```env
INCOME_TG_BOT_TOKEN=токен_от_BotFather
INCOME_TG_TELEGRAM_PROXY_URL=
INCOME_TG_TELEGRAM_OWNER_ID=1
```

`INCOME_TG_TELEGRAM_PROXY_URL` необязателен и применяется только к Telegram API. Оставьте его пустым, если сервер подключается к Telegram напрямую. Если прокси необходим, укажите SOCKS-адрес, доступный из контейнера `bot`, например `socks5://proxy-host:1080`. Адрес `172.17.0.1` подходит только для специально настроенного прокси на Docker-хосте и не должен копироваться без такой настройки.

### 3. Настроить пароль PostgreSQL

Сгенерировать пароль из безопасных для DSN символов:

```bash
openssl rand -hex 32
```

Записать одно и то же значение в `.env`:

```env
POSTGRES_PASSWORD=полученный_пароль
INCOME_TG_DATABASE_URL=postgresql+asyncpg://income_tg:полученный_пароль@postgres:5432/income_tg
INCOME_TG_BACKUP_DATABASE_DSN=postgresql://income_tg:полученный_пароль@postgres:5432/income_tg
```

Для production-запуска на VPS также установить:

```env
INCOME_TG_ENVIRONMENT=production
INCOME_TG_PAPER_ONLY=true
INCOME_TG_DEBUG=false
```

Без `INCOME_TG_ENVIRONMENT=production` команда production preflight намеренно завершится ошибкой.

Файл `.env` не попадает в Git. Не публикуйте токен бота, пароль базы или seed-фразу кошелька.

### 4. Узнать Telegram owner ID

Запустить минимальный набор сервисов:

```bash
docker compose up --build -d postgres init bot
```

Написать созданному боту `/id`. Он ответит вашим Telegram ID. Заменить временное значение в `.env`:

```env
INCOME_TG_TELEGRAM_OWNER_ID=ваш_telegram_id
```

Создать настоящего владельца и перезапустить бота:

```bash
docker compose run --rm init
docker compose up -d --force-recreate bot
```

Команда `/id` публична только для определения собственного ID. Все данные портфеля и остальные команды защищены проверкой owner ID.

### 5. Запустить весь paper-контур

```bash
docker compose up --build -d
```

Compose поднимает PostgreSQL, Telegram-бота, Bybit/OKX collectors, feature worker, scheduler переобучения, trading worker и readiness sidecar.

Bybit collector получает BTC, ETH и TON. Для OKX perpetual используются только BTC и ETH, потому что у OKX нет инструмента `TON-USDT-SWAP`.

### 6. Проверить состояние

```bash
docker compose ps
docker compose logs --tail 100 bot
docker compose logs --tail 100 collector-bybit collector-okx features scheduler worker
```

Сразу после чистого запуска `health` может иметь статус `unhealthy`: это ожидаемо, пока не накоплены размеченные наблюдения и не назначена champion-модель. Worker до этого момента не создаёт сделки.

### 7. Посмотреть результаты позже

Контейнеры работают в фоне после закрытия SSH-сессии. В Telegram доступны:

- `/status` — сервисы, свежесть данных, прогресс BTC/15m и активная модель;
- `/stats` — paper equity и просадка;
- `/signals` — последние сигналы;
- `/portfolio` — ручной и виртуальный портфели;
- `/risk` — действующие ограничения риска.

Текущий автоматический worker и обучение работают с `BTC/USDT:PERP` на горизонте `15m`. Для ETH собираются данные, пригодные для последующего отдельного обучения. Для TON Bybit сохраняет сырые рыночные данные, но feature pipeline и обучение TON пока отключены: на OKX отсутствует резервный инструмент `TON-USDT-SWAP`.

Для 15-минутной модели движение считается пригодным для сделки только после покрытия расчётных
комиссий и дополнительного запаса. Рабочий порог уверенности выбирается на calibration-части
обучающих данных для целевой доли действий 20%; test-часть используется только для независимой
проверки кандидата. Пока champion отсутствует, повторная попытка обучения выполняется раз в час.

### 8. Остановка и повторный запуск

```bash
docker compose stop
docker compose start
```

PostgreSQL и модели находятся в именованных Docker volumes и сохраняются при `stop`, `start`, `restart`, `up --build` и обычном `down`.

Не выполняйте следующую команду, если требуется сохранить историю:

```bash
docker compose down -v
```

Флаг `-v` удаляет базу, модели и накопленные результаты.

## Обновление без потери базы данных

```bash
git pull --ff-only
docker compose --profile operations run --rm backup
docker compose build
docker compose run --rm init
docker compose up -d --force-recreate
```

`init` применяет только новые Alembic-миграции и идемпотентно проверяет владельца и портфели. Перед обновлением production рекомендуется проверить резервную копию по [OPERATIONS.md](OPERATIONS.md).

## Production-проверка и резервное копирование

После появления champion-модели выполнить:

```bash
docker compose --profile production run --rm preflight
docker compose --profile operations run --rm backup
```

Ежедневный запуск backup-команды на VPS настраивается внешним cron или systemd timer согласно [OPERATIONS.md](OPERATIONS.md).

## Быстрый локальный запуск через Docker

1. Скопировать `.env.example` в `.env`.
2. Заполнить `INCOME_TG_BOT_TOKEN` и `INCOME_TG_TELEGRAM_OWNER_ID`.
3. Запустить:

```powershell
docker compose up --build -d
```

4. Проверить состояние:

```powershell
docker compose ps
docker compose logs -f bot
```

При старте `init` применяет Alembic-миграции и идемпотентно создаёт владельца и два портфеля.

## Локальная разработка

Создать окружение и установить зависимости:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
```

Запустить только PostgreSQL:

```powershell
docker compose up -d postgres
```

Создать `.env` и заменить host базы с `postgres` на `localhost`, затем выполнить:

```powershell
.venv\Scripts\alembic upgrade head
.venv\Scripts\income-tg-bootstrap
.venv\Scripts\income-tg-bot
```

## Команды бота

```text
/id
/help
/portfolio
/signals
/stats
/status
/risk
/setrisk max_leverage 10
/deposit USDT 100
/withdraw USDT 25
/buy BTC USDT 0.001 60000 0.06
/sell BTC USDT 0.001 65000 0.065
/reconcile BTC=0.01 USDT=500 RUB=1000
```

Основные разделы доступны и через постоянную клавиатуру. В интерактивной справке можно сразу открыть нужный экран, а для команд с параметрами — скопировать готовый пример.

`/reconcile` принимает полный снимок: ранее существовавший актив, не указанный в команде, будет обнулен. Реальные операции никогда не отправляются в Crypto Wallet — бот только отражает их в своем журнале.

## Проверки качества

```powershell
.venv\Scripts\ruff check .
.venv\Scripts\ruff format --check .
.venv\Scripts\mypy
.venv\Scripts\pytest
.venv\Scripts\alembic upgrade head --sql
```

## Документы

- [Техническое задание](TECHNICAL_SPECIFICATION.md)
- [Архитектура](ARCHITECTURE.md)
- [Схема базы данных](DATABASE_DESIGN.md)
- [План реализации](IMPLEMENTATION_PLAN.md)

## Безопасность

- Не добавлять `.env` в Git.
- Не передавать боту seed-фразу Crypto Wallet.
- В текущей версии не нужны биржевые API-ключи.
- Поле ручного курса RUB/USDT временное и не является рыночной котировкой.
