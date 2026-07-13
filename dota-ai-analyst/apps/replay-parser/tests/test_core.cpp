// Unit-тесты ядра парсера: varint/поля pb_lite и кадрирование DemoReader
// на синтетическом .dem, собранном в памяти (без внешних зависимостей).
#include <snappy.h>

#include <cassert>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>

#include "demo_reader.hpp"
#include "pb_lite.hpp"

namespace {

int g_failures = 0;

#define CHECK(cond)                                                          \
    do {                                                                     \
        if (!(cond)) {                                                       \
            std::fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
            g_failures++;                                                    \
        }                                                                    \
    } while (0)

void put_varint(std::string& out, uint64_t v) {
    while (v >= 0x80) {
        out.push_back(char(v & 0x7F) | char(0x80));
        v >>= 7;
    }
    out.push_back(char(v));
}

// -- pb_lite ------------------------------------------------------------

void test_varint_roundtrip() {
    for (uint64_t v : {0ull, 1ull, 127ull, 128ull, 300ull,
                       0xFFFFFFFFull, 0xFFFFFFFFFFFFFFFFull}) {
        std::string buf;
        put_varint(buf, v);
        dota::pb::Reader r(buf);
        auto got = r.varint();
        CHECK(got && *got == v);
        CHECK(r.eof());
    }
}

void test_varint_truncated() {
    std::string buf = "\xFF\xFF";  // продолжение без завершающего байта
    dota::pb::Reader r(buf);
    CHECK(!r.varint().has_value());
}

void test_fields() {
    // message { 1: varint 150; 2: bytes "abc"; 3: fixed32 7 }
    std::string msg;
    put_varint(msg, (1 << 3) | 0); put_varint(msg, 150);
    put_varint(msg, (2 << 3) | 2); put_varint(msg, 3); msg += "abc";
    put_varint(msg, (3 << 3) | 5); msg += std::string("\x07\x00\x00\x00", 4);

    dota::pb::Reader r(msg);
    dota::pb::Field f;
    CHECK(next_field(r, f) && f.number == 1 && f.varint == 150);
    CHECK(next_field(r, f) && f.number == 2 && f.data == "abc");
    CHECK(next_field(r, f) && f.number == 3 && f.varint == 7);
    CHECK(!next_field(r, f));
}

// -- DemoReader ----------------------------------------------------------

void append_frame(std::string& out, int32_t cmd, uint32_t tick,
                  const std::string& payload, bool compress) {
    uint64_t raw_cmd = uint64_t(cmd);
    std::string body = payload;
    if (compress) {
        std::string packed;
        snappy::Compress(payload.data(), payload.size(), &packed);
        body = packed;
        raw_cmd |= dota::demo::kCompressedFlag;
    }
    put_varint(out, raw_cmd);
    put_varint(out, tick);
    put_varint(out, body.size());
    out += body;
}

std::string build_synthetic_dem() {
    // CDemoFileHeader { 1: stamp, 5: map_name }
    std::string hdr_msg;
    put_varint(hdr_msg, (1 << 3) | 2); put_varint(hdr_msg, 8);
    hdr_msg += std::string("PBDEMS2\0", 8);
    put_varint(hdr_msg, (5 << 3) | 2); put_varint(hdr_msg, 6); hdr_msg += "dota_t";

    std::string frames;
    append_frame(frames, int32_t(dota::demo::Cmd::FileHeader), 0xFFFFFFFFu, hdr_msg, false);
    append_frame(frames, int32_t(dota::demo::Cmd::SyncTick), 0, "", false);
    std::string big(3000, 'x');  // сжимаемый кадр
    append_frame(frames, int32_t(dota::demo::Cmd::Packet), 30, big, true);
    append_frame(frames, int32_t(dota::demo::Cmd::Packet), 60, "tiny", false);

    // summary: CDemoFileInfo { 2: ticks=60, 4: CGameInfo{4: CDotaGameInfo{1: match_id}} }
    std::string dota_info;
    put_varint(dota_info, (1 << 3) | 0); put_varint(dota_info, 987654321);
    put_varint(dota_info, (3 << 3) | 0); put_varint(dota_info, 2);  // Radiant
    std::string game_info;
    put_varint(game_info, (4 << 3) | 2); put_varint(game_info, dota_info.size());
    game_info += dota_info;
    std::string file_info;
    put_varint(file_info, (2 << 3) | 0); put_varint(file_info, 60);
    put_varint(file_info, (4 << 3) | 2); put_varint(file_info, game_info.size());
    file_info += game_info;

    std::string out = "PBDEMS2";
    out.push_back('\0');
    uint32_t summary_off = uint32_t(16 + frames.size());
    for (int i = 0; i < 4; i++) out.push_back(char(summary_off >> (8 * i)));
    for (int i = 0; i < 4; i++) out.push_back(char(0));
    out += frames;
    append_frame(out, int32_t(dota::demo::Cmd::FileInfo), 60, file_info, false);
    append_frame(out, int32_t(dota::demo::Cmd::Stop), 60, "", false);
    return out;
}

void test_demo_reader_synthetic() {
    std::string dem = build_synthetic_dem();
    std::string path = "/tmp/synthetic_test.dem";
    std::ofstream(path, std::ios::binary).write(dem.data(), std::streamsize(dem.size()));

    dota::demo::DemoReader reader(path);

    auto hdr = reader.read_file_header();
    CHECK(hdr.map_name == "dota_t");

    auto stats = reader.scan();
    CHECK(stats.frames == 5);  // все кадры до Stop (FileInfo в summary тоже читается потоком)
    CHECK(stats.compressed_frames == 1);
    CHECK(stats.last_tick == 60);
    CHECK(stats.frames_by_cmd.at(int32_t(dota::demo::Cmd::Packet)) == 2);
    // снаппи-кадр отдан в распакованном виде
    CHECK(stats.decompressed_bytes == 3000);

    auto info = reader.read_file_info();
    CHECK(info.match_id == 987654321);
    CHECK(info.game_winner == 2);
    CHECK(info.playback_ticks == 60);

    std::remove(path.c_str());
}

void test_bad_magic_rejected() {
    std::string path = "/tmp/bad_magic.dem";
    std::ofstream(path, std::ios::binary) << "NOTADEMOxxxxxxxxxxxxxxxx";
    bool threw = false;
    try {
        dota::demo::DemoReader r(path);
    } catch (const std::exception&) {
        threw = true;
    }
    CHECK(threw);
    std::remove(path.c_str());
}

}  // namespace

int main() {
    test_varint_roundtrip();
    test_varint_truncated();
    test_fields();
    test_demo_reader_synthetic();
    test_bad_magic_rejected();
    if (g_failures == 0) {
        std::printf("ALL TESTS PASSED\n");
        return 0;
    }
    std::fprintf(stderr, "%d FAILURE(S)\n", g_failures);
    return 1;
}
