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

## Состояние: демукс пакетов и схема сущностей (спринт 6, часть 1) ✅

- **BitReader** — little-endian битовый ридер (read_bits, ubitvar, varint,
  не выровненные байтовые чтения).
- **packet_demux** — внутренний слой `DEM_Packet`/`DEM_SignonPacket`/`DEM_FullPacket`:
  `CDemoPacket.data` → поток сообщений `ubitvar type | varint size | payload`.
- **Схема сущностей**: `CDemoClassInfo` (class_id → имя) и
  `CSVCMsg_FlattenedSerializer` из `CDemoSendTables` (символы, поля с
  bit_count/low/high/encoder, сериализаторы с индексами полей, привязка
  вложенных сериализаторов).
- `demoinfo --deep` — гистограмма внутренних сообщений, имена string tables.

Замер на реплее 8892914077: **958 639 внутренних сообщений за 457 мс**;
схема: 3 229 классов, 3 294 сериализатора, 5 522 символа; найдены все
19 string tables (`CombatLogNames`, `instancebaseline`, `EntityNames`, ...);
сериализатор `CDOTA_Unit_Hero_Puck` содержит 183 поля.

## Состояние: string tables, combat log ✅; декодер сущностей 🔴 WIP (спринт 6, часть 2/3)

- **string_tables** — декодер `svc_Create/UpdateStringTable` (история ключей,
  user data, snappy); `CombatLogNames` разрешает 544 имени на реальном реплее.
- **combat_log** — `CMsgDOTACombatLogEntry` (msg id 554) с резолвом имён;
  на реплее 8892914077: 131 818 записей, 65 убийств героев с инфликторами.
  `demoinfo --events OUT.jsonl` пишет поток под схему `ReplayEvents`.
- **entities/fieldpath/field_decoder — НЕ РАБОТАЕТ, в разработке.**
  Реализованы: BitReader-совместимый декодер 40 field-path операций
  (huffman-дерево, портирован алгоритм построения кучи из `dotabuff/manta`
  для битовой совместимости с сетевым форматом), типовые декодеры полей
  (quantized float, coord, векторы, строки, handle/enum), резолвер путей по
  `SendTables` с учётом версий сериализаторов, машина состояний сущностей
  (create/update/delete, instancebaseline). На реальном реплее декодер
  **всё ещё расходится с потоком** (`DESYNC`, 0 обработанных пакетов) —
  последняя находка (аргумент `ubitvar` в операциях 21–24 использует базовый,
  а не FP-вариант кодирования) исправлена, но не проверена до конца.
  Инструмент отладки: `ENT_DEBUG=1|2|3 ./build/demoinfo --entities out.jsonl replay.dem`
  (уровни: команды сущностей / значения полей / операции field path).

## Дальше (спринт 6, часть 3 — доделать)

- Найти оставшуюся причину desync в декодере сущностей (вероятные места:
  семантика `PushN`/`PopN`-групп операций, разбор вложенных сериализаторов
  для массивов структур, кодировка `CUtlVector`).
- После чистого декода: извлечь позиции (`m_cellX/m_cellY/m_vecOrigin`) и
  экономику героев (`m_iNetWorth`, `m_iTotalEarnedGold`) в JSONL/ClickHouse.
- Go-обвязка: Kafka-консьюмер `match.downloaded` → ядро → `replay.parsed`.
