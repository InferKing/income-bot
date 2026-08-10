# Проектирование базы данных

Версия: 1.0
СУБД: PostgreSQL

## 1. Общие правила

- Первичные ключи бизнес-сущностей: UUID.
- Высокочастотные рыночные события могут использовать составной естественный ключ поставщика.
- Денежные значения, цены и количества: `NUMERIC`, не `FLOAT`.
- Модельные признаки и метрики могут использовать `DOUBLE PRECISION`, если это не расчет баланса.
- Все временные поля: `TIMESTAMPTZ` в UTC.
- Служебные JSON-поля: `JSONB`, но основные фильтруемые атрибуты хранятся отдельными колонками.
- Для неизменяемых финансовых событий запрещен обычный `UPDATE`; исправление создается новой записью.
- Миграции выполняются Alembic.

## 2. Перечисления

Предметные enum рекомендуется хранить как строки с `CHECK`, чтобы добавление нового значения не блокировало миграцию типа PostgreSQL.

Основные значения:

```text
market_type: SPOT | LINEAR_PERPETUAL
side: BUY | SELL
position_side: LONG | SHORT
portfolio_kind: REAL_MANUAL | PAPER
signal_action: BUY | LONG | SHORT | CLOSE | HOLD
signal_status: CANDIDATE | APPROVED | REJECTED | EXPIRED | CANCELLED | EXECUTED
order_status: CREATED | OPEN | FILLED | CANCELLED | REJECTED | EXPIRED
model_stage: CHALLENGER | CHAMPION | RETIRED | FAILED
health_status: HEALTHY | DEGRADED | DOWN
```

## 3. Справочники и настройки

### `users`

```text
id UUID PK
telegram_user_id BIGINT UNIQUE NOT NULL
language VARCHAR(8) NOT NULL DEFAULT 'ru'
timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Yekaterinburg'
is_active BOOLEAN NOT NULL DEFAULT TRUE
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

### `instruments`

```text
id UUID PK
canonical_symbol VARCHAR(32) NOT NULL
base_asset VARCHAR(16) NOT NULL
quote_asset VARCHAR(16) NOT NULL
market_type VARCHAR(32) NOT NULL
is_active BOOLEAN NOT NULL DEFAULT TRUE
price_scale INTEGER NOT NULL
quantity_scale INTEGER NOT NULL
min_quantity NUMERIC(38,18)
min_notional NUMERIC(38,18)
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
UNIQUE(canonical_symbol, market_type)
```

### `provider_instruments`

Связывает канонический инструмент с обозначением поставщика.

```text
id UUID PK
instrument_id UUID FK instruments
provider VARCHAR(32) NOT NULL
provider_symbol VARCHAR(64) NOT NULL
provider_category VARCHAR(32)
is_primary BOOLEAN NOT NULL DEFAULT FALSE
metadata JSONB NOT NULL DEFAULT '{}'
UNIQUE(provider, provider_symbol, provider_category)
```

### `risk_profiles`

```text
id UUID PK
user_id UUID FK users
name VARCHAR(64) NOT NULL
is_active BOOLEAN NOT NULL DEFAULT FALSE
max_margin_fraction NUMERIC(8,6) NOT NULL DEFAULT 0.10
max_stop_loss_fraction NUMERIC(8,6) NOT NULL DEFAULT 0.01
max_daily_loss_fraction NUMERIC(8,6) NOT NULL DEFAULT 0.05
max_drawdown_fraction NUMERIC(8,6) NOT NULL DEFAULT 0.15
max_open_positions SMALLINT NOT NULL DEFAULT 3
max_leverage NUMERIC(8,2) NOT NULL DEFAULT 20
min_signal_confidence NUMERIC(8,6) NOT NULL DEFAULT 0.70
settings JSONB NOT NULL DEFAULT '{}'
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

Ограничения: доли находятся в диапазоне `(0, 1]`, плечо — `[1, 20]`, число позиций положительное.

### `settings_audit`

```text
id UUID PK
user_id UUID FK users
entity_type VARCHAR(64) NOT NULL
entity_id UUID
old_value JSONB
new_value JSONB NOT NULL
changed_at TIMESTAMPTZ NOT NULL
source VARCHAR(32) NOT NULL
```

## 4. Рыночные данные

### `market_candles`

```text
provider VARCHAR(32) NOT NULL
instrument_id UUID NOT NULL
interval VARCHAR(8) NOT NULL
open_time TIMESTAMPTZ NOT NULL
close_time TIMESTAMPTZ NOT NULL
open NUMERIC(38,18) NOT NULL
high NUMERIC(38,18) NOT NULL
low NUMERIC(38,18) NOT NULL
close NUMERIC(38,18) NOT NULL
volume NUMERIC(38,18) NOT NULL
turnover NUMERIC(38,18)
is_closed BOOLEAN NOT NULL
received_at TIMESTAMPTZ NOT NULL
PRIMARY KEY(provider, instrument_id, interval, open_time)
```

Партиционирование по `open_time`, затем индекс `(instrument_id, interval, open_time DESC)`.

### `market_trades`

```text
provider VARCHAR(32) NOT NULL
instrument_id UUID NOT NULL
provider_trade_id VARCHAR(128) NOT NULL
traded_at TIMESTAMPTZ NOT NULL
side VARCHAR(8) NOT NULL
price NUMERIC(38,18) NOT NULL
quantity NUMERIC(38,18) NOT NULL
received_at TIMESTAMPTZ NOT NULL
sequence BIGINT
PRIMARY KEY(provider, instrument_id, provider_trade_id)
```

Партиционирование по `traded_at`. Retention сырых сделок задается отдельно; агрегаты хранятся дольше.

### `orderbook_snapshots`

```text
id BIGSERIAL
provider VARCHAR(32) NOT NULL
instrument_id UUID NOT NULL
captured_at TIMESTAMPTZ NOT NULL
sequence BIGINT
depth SMALLINT NOT NULL
bids JSONB NOT NULL
asks JSONB NOT NULL
best_bid NUMERIC(38,18) NOT NULL
best_ask NUMERIC(38,18) NOT NULL
spread_bps DOUBLE PRECISION NOT NULL
received_at TIMESTAMPTZ NOT NULL
PRIMARY KEY(id, captured_at)
```

Хранятся периодические снимки и рассчитанные микроструктурные признаки. Сохранение каждого delta в PostgreSQL не требуется для MVP.

### `derivatives_metrics`

```text
provider VARCHAR(32) NOT NULL
instrument_id UUID NOT NULL
observed_at TIMESTAMPTZ NOT NULL
funding_rate NUMERIC(24,18)
next_funding_at TIMESTAMPTZ
open_interest NUMERIC(38,18)
mark_price NUMERIC(38,18)
index_price NUMERIC(38,18)
basis_bps DOUBLE PRECISION
long_liquidations NUMERIC(38,18)
short_liquidations NUMERIC(38,18)
metadata JSONB NOT NULL DEFAULT '{}'
PRIMARY KEY(provider, instrument_id, observed_at)
```

### `fx_rates`

```text
base_currency VARCHAR(16) NOT NULL
quote_currency VARCHAR(16) NOT NULL
provider VARCHAR(32) NOT NULL
observed_at TIMESTAMPTZ NOT NULL
rate NUMERIC(38,18) NOT NULL
is_derived BOOLEAN NOT NULL DEFAULT FALSE
metadata JSONB NOT NULL DEFAULT '{}'
PRIMARY KEY(base_currency, quote_currency, provider, observed_at)
```

### `data_quality_events`

```text
id UUID PK
provider VARCHAR(32) NOT NULL
instrument_id UUID
event_type VARCHAR(64) NOT NULL
severity VARCHAR(16) NOT NULL
started_at TIMESTAMPTZ NOT NULL
resolved_at TIMESTAMPTZ
details JSONB NOT NULL DEFAULT '{}'
```

## 5. Портфели и ledger

### `portfolios`

```text
id UUID PK
user_id UUID FK users
kind VARCHAR(32) NOT NULL
name VARCHAR(64) NOT NULL
base_currency VARCHAR(16) NOT NULL
is_active BOOLEAN NOT NULL DEFAULT TRUE
created_at TIMESTAMPTZ NOT NULL
UNIQUE(user_id, kind, name)
```

### `portfolio_events` и `ledger_entries`

Единый неизменяемый журнал финансовых изменений реализуется заголовком события и набором проводок. Это позволяет одной сделке атомарно изменить несколько активов: например, увеличить BTC, уменьшить USDT и отдельно списать комиссию.

```text
id UUID PK
portfolio_id UUID FK portfolios
event_type VARCHAR(32) NOT NULL
instrument_id UUID FK instruments NULL
occurred_at TIMESTAMPTZ NOT NULL
recorded_at TIMESTAMPTZ NOT NULL
source VARCHAR(32) NOT NULL
idempotency_key VARCHAR(128) NOT NULL
reverses_event_id UUID FK portfolio_events NULL
metadata JSONB NOT NULL DEFAULT '{}'
UNIQUE(portfolio_id, idempotency_key)
```

```text
ledger_entries
id UUID PK
event_id UUID FK portfolio_events
asset VARCHAR(16) NOT NULL
amount NUMERIC(38,18) NOT NULL
entry_kind VARCHAR(32) NOT NULL
CHECK(amount <> 0)
```

Положительная проводка увеличивает остаток, отрицательная уменьшает. Цена, сторона, комиссия, связь с сигналом и другие атрибуты события хранятся в типизированном metadata до появления специализированных торговых таблиц.

Типы событий включают `DEPOSIT`, `WITHDRAWAL`, `TRADE`, `FEE`, `FUNDING`, `ADJUSTMENT`, `POSITION_OPEN`, `POSITION_CLOSE`, `LIQUIDATION`.

### `portfolio_snapshots`

```text
id UUID PK
portfolio_id UUID FK portfolios
as_of TIMESTAMPTZ NOT NULL
equity_usdt NUMERIC(38,18) NOT NULL
equity_rub NUMERIC(38,18) NOT NULL
cash JSONB NOT NULL
positions JSONB NOT NULL
fx_rate_id JSONB
source_event_id UUID
created_at TIMESTAMPTZ NOT NULL
UNIQUE(portfolio_id, as_of)
```

### `positions`

Материализованное текущее состояние, восстанавливаемое из ledger.

```text
id UUID PK
portfolio_id UUID FK portfolios
instrument_id UUID FK instruments
position_side VARCHAR(8) NOT NULL
status VARCHAR(16) NOT NULL
quantity NUMERIC(38,18) NOT NULL
average_entry_price NUMERIC(38,18) NOT NULL
allocated_margin NUMERIC(38,18)
leverage NUMERIC(8,2)
stop_loss NUMERIC(38,18)
take_profit NUMERIC(38,18)
opened_at TIMESTAMPTZ NOT NULL
closed_at TIMESTAMPTZ
realized_pnl NUMERIC(38,18) NOT NULL DEFAULT 0
version INTEGER NOT NULL DEFAULT 1
```

Уникальный частичный индекс запрещает две открытые позиции с одинаковыми `(portfolio_id, instrument_id, position_side)`.

## 6. Модели и признаки

### `feature_sets`

```text
id UUID PK
name VARCHAR(128) NOT NULL
version VARCHAR(64) NOT NULL
schema_hash VARCHAR(128) NOT NULL
definition JSONB NOT NULL
created_at TIMESTAMPTZ NOT NULL
UNIQUE(name, version)
```

### `feature_vectors`

```text
feature_set_id UUID NOT NULL
instrument_id UUID NOT NULL
horizon VARCHAR(16) NOT NULL
as_of TIMESTAMPTZ NOT NULL
values JSONB NOT NULL
data_cutoff TIMESTAMPTZ NOT NULL
quality_status VARCHAR(16) NOT NULL
PRIMARY KEY(feature_set_id, instrument_id, horizon, as_of)
```

Для массового обучения признаки могут экспортироваться в Parquet. PostgreSQL хранит воспроизводимый индекс и online-набор, но не обязан быть единственным feature store.

### `training_runs`

```text
id UUID PK
status VARCHAR(16) NOT NULL
feature_set_id UUID FK feature_sets
started_at TIMESTAMPTZ NOT NULL
finished_at TIMESTAMPTZ
train_from TIMESTAMPTZ NOT NULL
train_to TIMESTAMPTZ NOT NULL
validation_definition JSONB NOT NULL
parameters JSONB NOT NULL
metrics JSONB
error_message TEXT
code_version VARCHAR(128) NOT NULL
data_version VARCHAR(128) NOT NULL
```

### `model_versions`

```text
id UUID PK
training_run_id UUID FK training_runs
name VARCHAR(128) NOT NULL
version VARCHAR(64) NOT NULL
stage VARCHAR(16) NOT NULL
artifact_uri TEXT NOT NULL
artifact_hash VARCHAR(128) NOT NULL
metrics JSONB NOT NULL
calibration JSONB NOT NULL
created_at TIMESTAMPTZ NOT NULL
activated_at TIMESTAMPTZ
retired_at TIMESTAMPTZ
UNIQUE(name, version)
```

### `predictions`

```text
id UUID PK
model_version_id UUID FK model_versions
feature_set_id UUID FK feature_sets
instrument_id UUID FK instruments
market_type VARCHAR(32) NOT NULL
horizon VARCHAR(16) NOT NULL
as_of TIMESTAMPTZ NOT NULL
data_cutoff TIMESTAMPTZ NOT NULL
expected_return DOUBLE PRECISION
probability_up DOUBLE PRECISION NOT NULL
probability_down DOUBLE PRECISION NOT NULL
regime VARCHAR(32)
contributions JSONB NOT NULL
created_at TIMESTAMPTZ NOT NULL
```

Индекс: `(instrument_id, market_type, horizon, as_of DESC)`.

## 7. Сигналы и риск

### `signals`

```text
id UUID PK
portfolio_id UUID FK portfolios
instrument_id UUID FK instruments
prediction_id UUID FK predictions
action VARCHAR(16) NOT NULL
status VARCHAR(16) NOT NULL
confidence DOUBLE PRECISION NOT NULL
entry_price_from NUMERIC(38,18)
entry_price_to NUMERIC(38,18)
quantity NUMERIC(38,18)
position_value_usdt NUMERIC(38,18)
position_value_rub NUMERIC(38,18)
allocated_margin_usdt NUMERIC(38,18)
leverage NUMERIC(8,2)
stop_loss NUMERIC(38,18)
take_profit NUMERIC(38,18)
risk_reward_ratio DOUBLE PRECISION
horizon VARCHAR(16) NOT NULL
valid_until TIMESTAMPTZ NOT NULL
cancel_condition TEXT
explanation JSONB NOT NULL
risk_profile_snapshot JSONB NOT NULL
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

### `risk_decisions`

```text
id UUID PK
signal_id UUID FK signals
decision VARCHAR(16) NOT NULL
reason_codes JSONB NOT NULL
input_snapshot JSONB NOT NULL
calculated_values JSONB NOT NULL
created_at TIMESTAMPTZ NOT NULL
```

### `risk_blocks`

```text
id UUID PK
portfolio_id UUID FK portfolios
block_type VARCHAR(64) NOT NULL
started_at TIMESTAMPTZ NOT NULL
expires_at TIMESTAMPTZ
resolved_at TIMESTAMPTZ
resolution_source VARCHAR(32)
details JSONB NOT NULL
```

## 8. Paper trading

### `paper_orders`

```text
id UUID PK
portfolio_id UUID FK portfolios
signal_id UUID FK signals
instrument_id UUID FK instruments
side VARCHAR(8) NOT NULL
position_side VARCHAR(8)
order_type VARCHAR(16) NOT NULL
status VARCHAR(16) NOT NULL
quantity NUMERIC(38,18) NOT NULL
limit_price NUMERIC(38,18)
stop_price NUMERIC(38,18)
created_at TIMESTAMPTZ NOT NULL
expires_at TIMESTAMPTZ
updated_at TIMESTAMPTZ NOT NULL
idempotency_key VARCHAR(128) UNIQUE NOT NULL
```

### `paper_fills`

```text
id UUID PK
order_id UUID FK paper_orders
filled_at TIMESTAMPTZ NOT NULL
quantity NUMERIC(38,18) NOT NULL
reference_price NUMERIC(38,18) NOT NULL
fill_price NUMERIC(38,18) NOT NULL
fee NUMERIC(38,18) NOT NULL
slippage_bps DOUBLE PRECISION NOT NULL
market_event_ref JSONB NOT NULL
```

### `equity_curve`

```text
portfolio_id UUID NOT NULL
observed_at TIMESTAMPTZ NOT NULL
equity_usdt NUMERIC(38,18) NOT NULL
equity_rub NUMERIC(38,18) NOT NULL
cash_usdt NUMERIC(38,18) NOT NULL
unrealized_pnl NUMERIC(38,18) NOT NULL
realized_pnl NUMERIC(38,18) NOT NULL
drawdown_fraction DOUBLE PRECISION NOT NULL
PRIMARY KEY(portfolio_id, observed_at)
```

## 9. Уведомления и фоновые задачи

### `notification_outbox`

```text
id UUID PK
user_id UUID FK users
event_type VARCHAR(64) NOT NULL
deduplication_key VARCHAR(128) UNIQUE NOT NULL
priority SMALLINT NOT NULL
payload JSONB NOT NULL
status VARCHAR(16) NOT NULL DEFAULT 'PENDING'
attempts SMALLINT NOT NULL DEFAULT 0
next_attempt_at TIMESTAMPTZ NOT NULL
telegram_message_id BIGINT
created_at TIMESTAMPTZ NOT NULL
sent_at TIMESTAMPTZ
last_error TEXT
```

### `scheduled_jobs`

```text
id UUID PK
job_type VARCHAR(64) NOT NULL
deduplication_key VARCHAR(128) UNIQUE NOT NULL
payload JSONB NOT NULL
status VARCHAR(16) NOT NULL
scheduled_at TIMESTAMPTZ NOT NULL
started_at TIMESTAMPTZ
finished_at TIMESTAMPTZ
attempts SMALLINT NOT NULL DEFAULT 0
last_error TEXT
```

### `service_health`

```text
service VARCHAR(64) NOT NULL
instance_id VARCHAR(128) NOT NULL
status VARCHAR(16) NOT NULL
last_heartbeat_at TIMESTAMPTZ NOT NULL
details JSONB NOT NULL DEFAULT '{}'
PRIMARY KEY(service, instance_id)
```

## 10. Ключевые транзакции

### Регистрация ручной операции

В одной транзакции:

1. проверяется `idempotency_key`;
2. добавляется `portfolio_event`;
3. обновляется материализованная позиция;
4. создается snapshot при необходимости;
5. добавляется запись аудита/уведомления.

### Создание сигнала

В одной транзакции:

1. сохраняется prediction либо используется существующий;
2. создается signal;
3. сохраняется risk decision;
4. для одобренного paper-сигнала создается order;
5. создается notification outbox.

## 11. Retention

Начальные значения, уточняемые после измерения объема:

- свечи и derivatives metrics — бессрочно;
- сырые сделки — 90 дней, затем агрегаты;
- снимки стакана высокой частоты — 30 дней, агрегаты бессрочно;
- финансовые события, сигналы и модели — бессрочно;
- технические логи в БД — 30 дней;
- backup PostgreSQL — 14 ежедневных и 8 еженедельных копий.

Удаление market data выполняется только отдельной retention-задачей по явным партициям. Финансовые таблицы под retention не попадают.
