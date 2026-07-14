// demoinfo — CLI: сводка по файлу .dem (заголовок, матч-инфо, статистика кадров).
// Режим --deep дополнительно демультиплексирует внутренние сообщения
// DEM_Packet и загружает схему сущностей (ClassInfo + FlattenedSerializer).
#include <chrono>
#include <cstdio>
#include <cstring>
#include <map>
#include <set>
#include <string>

#include "demo_reader.hpp"
#include "packet_demux.hpp"
#include "pb_lite.hpp"

using dota::demo::DemoReader;

static const char* winner_name(int64_t w) {
    if (w == 2) return "Radiant";
    if (w == 3) return "Dire";
    return "Unknown";
}

static void deep_scan(DemoReader& reader) {
    using dota::demo::InnerMsg;
    namespace demo = dota::demo;
    namespace pb = dota::pb;

    std::map<uint32_t, uint64_t> inner_hist;
    std::set<std::string> string_tables;
    demo::ClassInfo class_info;
    demo::SendTables send_tables;
    uint64_t inner_total = 0;

    auto on_inner = [&](const InnerMsg& m) {
        inner_hist[m.type]++;
        inner_total++;
        if (m.type == 44) {  // svc_CreateStringTable { name = 1 }
            pb::Reader r(m.payload);
            pb::Field f;
            while (pb::next_field(r, f)) {
                if (f.number == 1 && f.wire_type == 2) {
                    string_tables.insert(std::string(f.data));
                    break;
                }
            }
        }
    };

    auto t0 = std::chrono::steady_clock::now();
    reader.scan([&](const demo::Frame& fr) {
        switch (demo::Cmd(fr.cmd)) {
            case demo::Cmd::Packet:
            case demo::Cmd::SignonPacket:
                demo::demux_packet(fr.payload, on_inner);
                break;
            case demo::Cmd::FullPacket: {
                // CDemoFullPacket { string_table = 1; packet = 2 }
                pb::Reader r(fr.payload);
                pb::Field f;
                while (pb::next_field(r, f)) {
                    if (f.number == 2 && f.wire_type == 2) {
                        demo::demux_packet(f.data, on_inner);
                    }
                }
                break;
            }
            case demo::Cmd::ClassInfo:
                class_info = demo::parse_class_info(fr.payload);
                break;
            case demo::Cmd::SendTables:
                send_tables = demo::parse_send_tables(fr.payload);
                break;
            default:
                break;
        }
    });
    auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                  std::chrono::steady_clock::now() - t0).count();

    std::printf("== Deep scan ==\n");
    std::printf("  inner_messages : %llu (time %lld ms)\n",
                (unsigned long long)inner_total, (long long)ms);
    std::printf("  classes        : %zu\n", class_info.classes.size());
    std::printf("  serializers    : %zu (fields %zu, symbols %zu)\n",
                send_tables.serializers.size(), send_tables.fields.size(),
                send_tables.symbols.size());
    std::printf("  string_tables  : %zu\n", string_tables.size());
    for (const auto& n : string_tables) std::printf("    %s\n", n.c_str());
    std::printf("  inner_by_type  :\n");
    for (const auto& [type, n] : inner_hist) {
        const char* name = demo::inner_msg_name(type);
        std::printf("    %-36s (%3u) %llu\n", name ? name : "?", type,
                    (unsigned long long)n);
    }
    // Sanity: сериализатор героя должен присутствовать в схеме.
    auto it = send_tables.by_name.find("CDOTA_Unit_Hero_Puck");
    if (it != send_tables.by_name.end()) {
        const auto& s = send_tables.serializers[it->second];
        std::printf("  sample class   : %s (fields %zu)\n", s.name.c_str(),
                    s.field_indexes.size());
    }
}

int main(int argc, char** argv) {
    bool deep = false;
    const char* path = nullptr;
    for (int i = 1; i < argc; i++) {
        if (std::strcmp(argv[i], "--deep") == 0) deep = true;
        else path = argv[i];
    }
    if (!path) {
        std::fprintf(stderr, "usage: %s [--deep] <replay.dem>\n", argv[0]);
        return 2;
    }
    try {
        DemoReader reader(path);

        auto header = reader.read_file_header();
        std::printf("== FileHeader ==\n");
        std::printf("  stamp            : %s\n", header.demo_file_stamp.c_str());
        std::printf("  map              : %s\n", header.map_name.c_str());
        std::printf("  server           : %s\n", header.server_name.c_str());
        std::printf("  network_protocol : %lld\n", (long long)header.network_protocol);
        std::printf("  build            : %lld\n", (long long)header.build_num);

        auto info = reader.read_file_info();
        std::printf("== FileInfo ==\n");
        std::printf("  match_id       : %llu\n", (unsigned long long)info.match_id);
        std::printf("  winner         : %s\n", winner_name(info.game_winner));
        std::printf("  game_mode      : %lld\n", (long long)info.game_mode);
        std::printf("  playback_time  : %.1f s (%lld ticks, %lld frames)\n",
                    info.playback_time_s, (long long)info.playback_ticks,
                    (long long)info.playback_frames);
        std::printf("  players        : %zu\n", info.players.size());
        for (const auto& p : info.players) {
            std::printf("    [team %lld] %-25s %s\n", (long long)p.game_team,
                        p.player_name.c_str(), p.hero_name.c_str());
        }

        auto t0 = std::chrono::steady_clock::now();
        auto st = reader.scan();
        auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                      std::chrono::steady_clock::now() - t0).count();

        std::printf("== Scan ==\n");
        std::printf("  file_size      : %.1f MiB\n", reader.file_size() / 1048576.0);
        std::printf("  frames         : %llu (%llu snappy-compressed)\n",
                    (unsigned long long)st.frames,
                    (unsigned long long)st.compressed_frames);
        std::printf("  payload        : %.1f MiB (decompressed %.1f MiB)\n",
                    st.payload_bytes / 1048576.0, st.decompressed_bytes / 1048576.0);
        std::printf("  last_tick      : %u\n", st.last_tick);
        std::printf("  scan_time      : %lld ms (%.1f MiB/s)\n", (long long)ms,
                    reader.file_size() / 1048576.0 / (ms / 1000.0));
        std::printf("  frames_by_cmd  :\n");
        for (const auto& [cmd, n] : st.frames_by_cmd) {
            std::printf("    %-24s %llu\n", dota::demo::cmd_name(cmd),
                        (unsigned long long)n);
        }
        if (deep) deep_scan(reader);
        return 0;
    } catch (const std::exception& e) {
        std::fprintf(stderr, "error: %s\n", e.what());
        return 1;
    }
}
