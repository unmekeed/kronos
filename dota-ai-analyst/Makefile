# Корневые цели монорепозитория Dota AI Analyst (Гл. 13.7 спецификации).

GO_SERVICES := apps/api-gateway
COMPOSE     := docker compose -f deployments/docker-compose.yml

.PHONY: up down ps topics migrate migrate-pg migrate-ch lint test build clean

## Инфраструктура -------------------------------------------------------------

up:            ## Поднять локальную инфраструктуру (PG, CH, Kafka, Redis, MinIO)
	$(COMPOSE) up -d
	$(COMPOSE) ps

down:          ## Остановить инфраструктуру (данные сохраняются в volumes)
	$(COMPOSE) down

ps:            ## Статус контейнеров
	$(COMPOSE) ps

topics:        ## Создать Kafka-топики по реестру Гл. 2.3.1
	./infra/kafka/create-topics.sh

migrate: migrate-pg migrate-ch  ## Применить все миграции

migrate-pg:    ## Миграции PostgreSQL
	PGPASSWORD=dota_dev_password psql -h localhost -U dota -d dota_analyst \
		-v ON_ERROR_STOP=1 -f infra/migrations/postgres/001_init.sql

migrate-ch:    ## Миграции ClickHouse
	docker exec -i dota-ai-analyst-clickhouse-1 clickhouse-client \
		--user dota --password dota_dev_password --multiquery \
		< infra/migrations/clickhouse/001_init.sql

## Код -------------------------------------------------------------------------

lint:          ## Статический анализ Go-сервисов
	@for s in $(GO_SERVICES); do \
		echo ">> vet $$s"; (cd $$s && go vet ./...) || exit 1; \
	done

test:          ## Unit-тесты
	@for s in $(GO_SERVICES); do \
		echo ">> test $$s"; (cd $$s && go test ./...) || exit 1; \
	done

build:         ## Сборка бинарей
	@for s in $(GO_SERVICES); do \
		echo ">> build $$s"; (cd $$s && go build ./...) || exit 1; \
	done

clean:
	rm -rf bin/

## Replay Parser (C++) ---------------------------------------------------------

parser-build:  ## Собрать ядро парсера и CLI demoinfo
	cmake -B apps/replay-parser/build -S apps/replay-parser -DCMAKE_BUILD_TYPE=Release
	cmake --build apps/replay-parser/build -j4

parser-test: parser-build  ## Unit-тесты ядра парсера
	ctest --test-dir apps/replay-parser/build --output-on-failure
