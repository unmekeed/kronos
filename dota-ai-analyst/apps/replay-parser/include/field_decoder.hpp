// field_decoder — типовые декодеры значений полей Source 2 и разрешение
// пути поля через дерево сериализаторов (Гл. 5.1).
// Значение любого поля обязано быть декодировано корректно по типу —
// иначе битовый поток теряет синхронизацию.
#pragma once

#include <cstdint>
#include <string>
#include <unordered_map>
#include <variant>

#include "bit_reader.hpp"
#include "fieldpath.hpp"
#include "packet_demux.hpp"

namespace dota::demo {

using FieldValue = std::variant<std::monostate, bool, uint64_t, int64_t,
                                float, std::string>;

// Результат разрешения пути: как декодировать значение в этой позиции.
struct ResolvedField {
    enum class Kind {
        Bool, VarUint, VarSint, Fixed64, Float, Vector2, Vector3, Vector4,
        QAngle, NormalVec, String, ArrayCount, PointerMarker, Unknown,
    };
    Kind kind = Kind::Unknown;
    // Параметры float-декодера.
    int bit_count = 0;
    float low = 0.0f, high = 1.0f;
    int encode_flags = 0;
    bool coord = false;      // encoder "coord"
    bool simtime = false;    // simulation time (varint / 30)
    std::string full_name;   // "CBodyComponent.m_cellX" (для watched-полей)
};

// Декодер значения по ResolvedField; пишет в out (для векторов — X-компонент,
// остальные компоненты потребляются из потока для сохранения синхронизации).
bool decode_value(bits::BitReader& r, const ResolvedField& f, FieldValue& out);

// Резолвер путей по схеме SendTables: класс → сериализатор → поле.
class FieldResolver {
  public:
    explicit FieldResolver(const SendTables& st) : st_(st) {}

    // Разрешить путь fp относительно сериализатора ser_idx.
    // Возвращает false при несуществующем пути (desync).
    bool resolve(size_t ser_idx, const FieldPath& fp, ResolvedField& out) const;

  private:
    const SendTables& st_;
    // Кэш: (serializer, path key) → ResolvedField.
    mutable std::unordered_map<uint64_t, ResolvedField> cache_;
};

}  // namespace dota::demo
