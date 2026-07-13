# Dota AI Analyst — Platform Monorepo

Реализация интеллектуальной платформы анализа матчей Dota 2 по
[спецификации v2.0.0](../docs/specification/ru/README.md).

## Статус разработки (Roadmap Гл. 14)

| Фаза | Состояние | Содержание |
|---|---|---|
| **Фаза 1: Инфраструктура** | 🟡 в работе (спринты 1–2 ✅) | compose-инфраструктура, миграции PG/CH, Kafka-топики, каркас API Gateway |
| Фаза 2: Парсинг и ETL | ⚪ не начата | Replay Parser (C++), Data Collector, ETL |
| Фаза 3: Аналитика и ML | ⚪ не начата | Feature Store, WP/Laning/Draft/Error модели |
| Фаза 4: UI, MLOps, Релиз | ⚪ не начата | Frontend, дрейф-мониторинг, нагрузочные тесты |

### Что уже работает (проверено против живой инфраструктуры)

- `deployments/docker-compose.yml` — PostgreSQL 16, ClickHouse 24.8, Kafka 3.8 (KRaft), Redis 7, MinIO; все с healthcheck.
- `infra/migrations/` — реляционная схема Гл. 4.2 (7 таблиц, enum-типы, индексы) и аналитическая схема Гл. 4.4 (ReplayEvents, EconomyTimeline, PositionSnapshots).
- `infra/kafka/create-topics.sh` — 7 топиков реестра Гл. 2.3.1 с retention-политиками.
- `libs/schemas/event-envelope.schema.json` — JSON Schema конверта события Гл. 2.3.3.
- `apps/api-gateway` — Go-сервис: `/healthz`, `/readyz` (ping PG), `POST /api/v1/matches/upload` (202 + job в PG), `GET /api/v1/jobs/{id}`, ошибки RFC 7807, trace_id (W3C traceparent), структурированные JSON-логи, in-memory token-bucket rate limit; unit-тесты middleware; distroless Dockerfile.

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

## Следующие шаги

1. Фаза 1, спринты 3–4: обвязка Data Collector (публикация `match.downloaded`), выгрузка `.dem` в MinIO из шлюза, Schema Registry.
2. Фаза 2: ядро Replay Parser (C++), ETL-конвейер до ClickHouse.
