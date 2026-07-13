# Replay Parser

Ядро низкоуровневого разбора файлов Source 2 Demo (`.dem`) — Гл. 5 спецификации.

## Состояние: DemoReader (спринт 5) ✅

Реализован внешний слой формата:

- **DemoReader** — mmap-чтение, покадровая итерация (`varint cmd | tick | size | payload`),
  прозрачная распаковка snappy-кадров (флаг `0x40`).
- **pb_lite** — минимальный ридер wire-формата Protobuf для служебных сообщений
  (без кодогенерации; полноценные `.proto` подключаются на этапе EntityDecoder).
- Разбор **CDemoFileHeader** (карта, сервер, билд) и **CDemoFileInfo** (match_id,
  победитель, тайминги, ростер игроков с героями и командами).
- CLI **demoinfo** — сводка по реплею + статистика кадров.

Замер на реальном реплее матча **8892914077** (110.6 МиБ, 74:57 игрового времени,
67 542 кадра, 33 915 сжатых): полный проход с распаковкой — **62 мс (~1.8 ГиБ/с)**.
Эталонный файл хранится в dev-MinIO: `s3://replays/fixtures/8892914077.dem`.

## Сборка и тесты

```bash
apt-get install -y libsnappy-dev cmake g++
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release
cmake --build build -j4
ctest --test-dir build          # unit-тесты (varint, pb-поля, синтетический .dem)
./build/demoinfo replay.dem     # сводка по реальному файлу
```

## Дальше (спринт 6)

- **EntityDecoder**: разбор `DEM_SendTables`/`DEM_ClassInfo` (flattened serializers),
  string tables, чтение baseline'ов и delta-обновлений сущностей из `DEM_Packet`.
- Извлечение позиций (`m_cellX/m_cellY/m_vecOrigin`) и экономики игроков.
- Go-обвязка: Kafka-консьюмер `match.downloaded` → вызов ядра → `replay.parsed`.
