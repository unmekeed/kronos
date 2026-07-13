# Dota AI Analyst — Platform Monorepo

Реализация интеллектуальной платформы анализа матчей Dota 2 по
[спецификации v2.0.0](../docs/specification/ru/README.md).

## Статус разработки (Roadmap Гл. 14)

| Фаза | Состояние | Содержание |
|---|---|---|
| **Фаза 1: Инфраструктура** | ✅ завершена (спринты 1–4) | compose-инфраструктура, миграции PG/CH, Kafka-топики, API Gateway (S3+outbox), Data Collector |
| Фаза 2: Парсинг и ETL | 🟡 в работе (спринт 5 ✅) | Replay Parser (C++): DemoReader готов, проверен на реальном 110МБ реплее (62 мс/проход) |
| Фаза 3: Аналитика и ML | ⚪ не начата | Feature Store, WP/Laning/Draft/Error модели |
| Фаза 4: UI, MLOps, Релиз | ⚪ не начата | Frontend, дрейф-мониторинг, нагрузочные тесты |

### Что уже работает (проверено против живой инфраструктуры)

- `deployments/docker-compose.yml` — PostgreSQL 16, ClickHouse 24.8, Kafka 3.8 (KRaft), Redis 7, MinIO; все с healthcheck.
- `infra/migrations/` — реляционная схема Гл. 4.2 (7 таблиц, enum-типы, индексы) и аналитическая схема Гл. 4.4 (ReplayEvents, EconomyTimeline, PositionSnapshots).
- `infra/kafka/create-topics.sh` — 7 топиков реестра Гл. 2.3.1 с retention-политиками.
- `libs/schemas/event-envelope.schema.json` — JSON Schema конверта события Гл. 2.3.3.
- `apps/api-gateway` — Go-сервис: `/healthz`, `/readyz` (ping PG), `POST /api/v1/matches/upload` (файл → MinIO, job + outbox-событие в одной PG-транзакции → 202), `GET /api/v1/jobs/{id}`, ошибки RFC 7807, trace_id (W3C traceparent), JSON-логи, token-bucket rate limit; фоновый **outbox-relay** публикует события в Kafka (`FOR UPDATE SKIP LOCKED`, безопасен при нескольких репликах); unit-тесты; distroless Dockerfile.
- `apps/data-collector` — Python-сервис: абстракция `Source` (ACL, Гл. 2.5) с реализациями `OpenDotaSource` (pull по match_id-курсору) и `FixtureSource` (dev/тесты); дедупликация по `CollectedMatches`, курсор в `CollectorCursor`, выгрузка `.dem` в MinIO, публикация `match.downloaded`; unit-тесты; Dockerfile.

## Быстрый старт

```bash
make up        # поднять инфраструктуру
make migrate   # применить миграции PG + CH
make topics    # создать Kafka-топики

# запустить шлюз
cd apps/api-gateway && go run ./cmd/server

# проверить
curl localhost:8080/healthz
curl -X POST localhost:8080/api/v1/matches/upload -F "file=@replay.dem"
```

## Структура

Соответствует Гл. 13 спецификации: `apps/` (12 сервисов), `libs/` (общие схемы и библиотеки),
`proto/` + `openapi/` (контракты — источник истины), `infra/` (миграции, топики, terraform),
`deployments/` (compose, helm, k8s).

- `apps/replay-parser` — C++17-ядро: DemoReader (mmap, покадровая итерация, snappy), pb_lite (protobuf wire-формат без protoc), разбор CDemoFileHeader/CDemoFileInfo, CLI `demoinfo`; unit-тесты на синтетическом `.dem`. Реальный реплей 8892914077 (110.6 МиБ) читается за 62 мс; файл-эталон в dev-MinIO `s3://replays/fixtures/8892914077.dem`.

## Следующие шаги

1. Фаза 2 (спринт 6): EntityDecoder — SendTables/flattened serializers, string tables, позиции и экономика из `DEM_Packet`.
2. Фаза 2 (спринты 7–8): Go-обвязка парсера (Kafka), ETL-конвейер `replay.parsed` → валидация → ClickHouse/PostgreSQL → `features.calculated`.
