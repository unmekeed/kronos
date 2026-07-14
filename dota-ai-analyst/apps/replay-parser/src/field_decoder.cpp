#include "field_decoder.hpp"

#include <cmath>
#include <cstring>

namespace dota::demo {

namespace {

// -- Квантованный float (QuantizedFloatDecoder Source 2) ---------------------

constexpr int kQFRoundDown = 1 << 0;
constexpr int kQFRoundUp = 1 << 1;
constexpr int kQFEncodeZero = 1 << 2;
constexpr int kQFEncodeInt = 1 << 3;

struct QuantizedParams {
    int bits = 0;
    float low = 0, high = 1;
    int flags = 0;
    float decode_mul = 0, decode_div = 0;  // производные
    uint32_t steps = 0;

    void init() {
        // Валидация/нормализация флагов как в движке.
        int f = flags;
        if (low == 0.0f && (f & kQFRoundDown)) f &= ~kQFRoundDown;
        if (high == 0.0f && (f & kQFRoundUp)) f &= ~kQFRoundUp;
        if (low > 0.0f || high < 0.0f) f &= ~kQFEncodeZero;
        if ((f & kQFEncodeInt) &&
            (low != std::floor(low) || high != std::floor(high) ||
             high - low < 1.0f)) {
            // ENCODE_INT неприменим — снимается движком; здесь упрощённо
            f &= ~kQFEncodeInt;
        }
        flags = f;

        steps = (1u << bits) - 1;
        float range = high - low;
        if (flags & kQFRoundDown) {
            float delta = range / float(steps + 1);
            high -= delta * 0 + range / float(1u << bits);
        } else if (flags & kQFRoundUp) {
            low += range / float(1u << bits);
        }
        range = high - low;
        decode_mul = 1.0f / float(steps);
        decode_div = range;
    }

    float decode(bits::BitReader& r) const {
        if ((flags & kQFRoundDown) && r.read_bool()) return low;
        if ((flags & kQFRoundUp) && r.read_bool()) return high;
        if ((flags & kQFEncodeZero) && r.read_bool()) return 0.0f;
        uint32_t u = r.read_bits(uint32_t(bits));
        return low + float(u) * decode_mul * decode_div;
    }
};

float read_noscale_float(bits::BitReader& r) {
    uint32_t b = r.read_bits(32);
    float v;
    std::memcpy(&v, &b, sizeof v);
    return v;
}

// readCoord Source (целая часть 14 бит + дробь 5 бит).
float read_coord(bits::BitReader& r) {
    float value = 0;
    bool has_int = r.read_bool();
    bool has_frac = r.read_bool();
    if (!has_int && !has_frac) return 0;
    bool sign = r.read_bool();
    if (has_int) value += float(r.read_bits(14)) + 1;
    if (has_frac) value += float(r.read_bits(5)) * (1.0f / 32.0f);
    return sign ? -value : value;
}

// Нормализованный вектор (3D): 2 флага + 11-битные компоненты + знак Z.
void read_normal_vector(bits::BitReader& r) {
    bool has_x = r.read_bool();
    bool has_y = r.read_bool();
    auto read_norm = [&](bool) {
        bool sign = r.read_bool();
        uint32_t frac = r.read_bits(11);
        (void)sign;
        (void)frac;
    };
    if (has_x) read_norm(true);
    if (has_y) read_norm(true);
    r.read_bool();  // знак Z (Z восстанавливается из нормы)
}

int64_t zigzag(uint64_t v) { return int64_t(v >> 1) ^ -int64_t(v & 1); }

// -- Разбор строк типов -------------------------------------------------------

struct ParsedType {
    std::string base;       // тип без [] и generic-обёрток
    std::string element;    // тип элемента массива/вектора
    int fixed_array = 0;    // N для T[N]
    bool utl_vector = false;
    bool pointer = false;
};

ParsedType parse_var_type(const std::string& t) {
    ParsedType p;
    std::string s = t;
    if (!s.empty() && s.back() == '*') {
        p.pointer = true;
        s.pop_back();
    }
    auto lb = s.find('[');
    if (lb != std::string::npos) {
        p.fixed_array = std::atoi(s.substr(lb + 1).c_str());
        s = s.substr(0, lb);
    }
    for (const char* vec : {"CUtlVector< ", "CNetworkUtlVectorBase< ",
                            "CUtlVectorEmbeddedNetworkVar< "}) {
        if (s.rfind(vec, 0) == 0) {
            p.utl_vector = true;
            s = s.substr(std::strlen(vec));
            auto gt = s.rfind(" >");
            if (gt != std::string::npos) s = s.substr(0, gt);
            break;
        }
    }
    p.base = s;
    p.element = s;
    return p;
}

ResolvedField::Kind base_kind(const std::string& b) {
    using K = ResolvedField::Kind;
    if (b == "bool") return K::Bool;
    if (b == "uint8" || b == "uint16" || b == "uint32" || b == "uint64" ||
        b == "Color" || b == "color32" || b == "CUtlStringToken" ||
        b == "HSequence" || b == "CEntityHandle" ||
        b == "CGameSceneNodeHandle" || b.rfind("CHandle<", 0) == 0 ||
        b.rfind("CStrongHandle<", 0) == 0 || b == "item_definition_index_t" ||
        b == "itemid_t" || b == "style_index_t" || b == "CEntityIndex")
        return K::VarUint;
    if (b == "int8" || b == "int16" || b == "int32" || b == "int64")
        return K::VarSint;
    if (b == "float32" || b == "CNetworkedQuantizedFloat" || b == "GameTime_t" ||
        b == "float")
        return K::Float;
    if (b == "Vector") return K::Vector3;
    if (b == "Vector2D") return K::Vector2;
    if (b == "Vector4D" || b == "Quaternion") return K::Vector4;
    if (b == "QAngle") return K::QAngle;
    if (b == "CUtlString" || b == "CUtlSymbolLarge" || b == "char")
        return K::String;
    // Перечисления и неизвестные скаляры кодируются varint.
    return K::VarUint;
}

}  // namespace

bool decode_value(bits::BitReader& r, const ResolvedField& f, FieldValue& out) {
    using K = ResolvedField::Kind;
    out = std::monostate{};

    auto read_float_one = [&]() -> float {
        if (f.coord) return read_coord(r);
        if (f.simtime) return float(r.read_varuint32()) * (1.0f / 30.0f);
        if (f.bit_count <= 0 || f.bit_count >= 32) return read_noscale_float(r);
        QuantizedParams q;
        q.bits = f.bit_count;
        q.low = f.low;
        q.high = f.high;
        q.flags = f.encode_flags;
        q.init();
        return q.decode(r);
    };

    switch (f.kind) {
        case K::Bool: out = r.read_bool(); break;
        case K::VarUint: out = r.read_varuint64(); break;
        case K::VarSint: out = zigzag(r.read_varuint64()); break;
        case K::Fixed64: {
            uint64_t lo = r.read_bits(32), hi = r.read_bits(32);
            out = (hi << 32) | lo;
            break;
        }
        case K::Float: out = read_float_one(); break;
        case K::Vector2: {
            float x = read_float_one();
            read_float_one();
            out = x;
            break;
        }
        case K::Vector3: {
            float x = read_float_one();
            read_float_one();
            read_float_one();
            out = x;
            break;
        }
        case K::Vector4: {
            float x = read_float_one();
            read_float_one(); read_float_one(); read_float_one();
            out = x;
            break;
        }
        case K::QAngle: {
            if (f.bit_count != 0) {
                r.read_bits(uint32_t(f.bit_count));
                r.read_bits(uint32_t(f.bit_count));
                r.read_bits(uint32_t(f.bit_count));
            } else {
                bool hx = r.read_bool(), hy = r.read_bool(), hz = r.read_bool();
                if (hx) read_coord(r);
                if (hy) read_coord(r);
                if (hz) read_coord(r);
            }
            out = 0.0f;
            break;
        }
        case K::NormalVec: read_normal_vector(r); out = 0.0f; break;
        case K::String: {
            std::string s;
            for (int i = 0; i < 4096; i++) {
                uint8_t c = uint8_t(r.read_bits(8));
                if (c == 0 || r.overflowed()) break;
                s.push_back(char(c));
            }
            out = std::move(s);
            break;
        }
        case K::ArrayCount: out = uint64_t(r.read_varuint32()); break;
        case K::PointerMarker: out = r.read_bool(); break;
        case K::Unknown: return false;
    }
    return !r.overflowed();
}

bool FieldResolver::resolve(size_t ser_idx, const FieldPath& fp,
                            ResolvedField& out) const {
    uint64_t key = (uint64_t(ser_idx) << 56) ^ fp.key();
    auto it = cache_.find(key);
    if (it != cache_.end()) {
        out = it->second;
        return out.kind != ResolvedField::Kind::Unknown;
    }

    ResolvedField rf;
    size_t cur_ser = ser_idx;
    std::string name;
    int32_t depth = 0;
    const SerializerField* fld = nullptr;
    bool in_array_elem = false;
    ParsedType pt;

    while (depth <= fp.last) {
        int32_t comp = fp.path[size_t(depth)];
        if (!in_array_elem) {
            if (cur_ser >= st_.serializers.size()) { rf.kind = ResolvedField::Kind::Unknown; break; }
            const auto& ser = st_.serializers[cur_ser];
            if (comp < 0 || size_t(comp) >= ser.field_indexes.size()) {
                rf.kind = ResolvedField::Kind::Unknown; break;
            }
            fld = &st_.fields[size_t(ser.field_indexes[size_t(comp)])];
            if (!name.empty()) name += '.';
            name += fld->var_name;
            pt = parse_var_type(fld->var_type);
            depth++;

            bool is_array = pt.fixed_array > 0 || pt.utl_vector;
            if (depth <= fp.last) {
                if (is_array) { in_array_elem = true; continue; }
                if (fld->field_serializer >= 0) {
                    cur_ser = size_t(fld->field_serializer);
                    continue;
                }
                rf.kind = ResolvedField::Kind::Unknown;  // путь глубже скаляра
                break;
            }
            // Путь закончился на самом поле.
            if (pt.utl_vector) { rf.kind = ResolvedField::Kind::ArrayCount; break; }
            if (pt.fixed_array > 0 && pt.base != "char") {
                rf.kind = ResolvedField::Kind::ArrayCount; break;
            }
            if (fld->field_serializer >= 0 || pt.pointer) {
                rf.kind = ResolvedField::Kind::PointerMarker; break;
            }
            rf.kind = base_kind(pt.base);
            break;
        } else {
            // Компонент — индекс элемента массива.
            name += '.';
            name += std::to_string(comp);
            depth++;
            in_array_elem = false;
            if (depth <= fp.last) {
                if (fld && fld->field_serializer >= 0) {
                    cur_ser = size_t(fld->field_serializer);
                    continue;
                }
                rf.kind = ResolvedField::Kind::Unknown;
                break;
            }
            // Элемент массива — конец пути.
            if (fld && fld->field_serializer >= 0) {
                rf.kind = ResolvedField::Kind::PointerMarker;
            } else {
                rf.kind = base_kind(pt.element);
            }
            break;
        }
    }

    if (fld && rf.kind != ResolvedField::Kind::Unknown) {
        rf.bit_count = fld->bit_count;
        rf.low = fld->low_value;
        rf.high = fld->high_value;
        rf.encode_flags = fld->encode_flags;
        rf.coord = fld->encoder == "coord";
        rf.simtime = fld->encoder == "simulationtime" ||
                     fld->var_type == "GameTime_t" ||
                     // Патч схемы: поля времени симуляции кодируются varint
                     // (tick/30), несмотря на заявленный float32.
                     fld->var_name == "m_flSimulationTime" ||
                     fld->var_name == "m_flAnimTime";
        if (fld->encoder == "normal" &&
            rf.kind == ResolvedField::Kind::Vector3) {
            rf.kind = ResolvedField::Kind::NormalVec;
        }
        if (fld->encoder == "fixed64" &&
            rf.kind == ResolvedField::Kind::VarUint) {
            rf.kind = ResolvedField::Kind::Fixed64;
        }
        if (fld->encoder == "qangle_pitch_yaw" &&
            rf.kind == ResolvedField::Kind::QAngle) {
            // pitch+yaw по bit_count, roll отсутствует
            rf.kind = ResolvedField::Kind::Vector2;  // 2 квантованных
        }
    }
    rf.full_name = std::move(name);
    cache_[key] = rf;
    out = cache_[key];
    return out.kind != ResolvedField::Kind::Unknown;
}

}  // namespace dota::demo
