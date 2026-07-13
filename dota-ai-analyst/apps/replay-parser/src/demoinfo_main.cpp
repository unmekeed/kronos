// demoinfo — CLI: сводка по файлу .dem (заголовок, матч-инфо, статистика кадров).
#include <chrono>
#include <cstdio>
#include <string>

#include "demo_reader.hpp"

using dota::demo::DemoReader;

static const char* winner_name(int64_t w) {
    if (w == 2) return "Radiant";
    if (w == 3) return "Dire";
    return "Unknown";
}

int main(int argc, char** argv) {
    if (argc != 2) {
        std::fprintf(stderr, "usage: %s <replay.dem>\n", argv[0]);
        return 2;
    }
    try {
        DemoReader reader(argv[1]);

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
        return 0;
    } catch (const std::exception& e) {
        std::fprintf(stderr, "error: %s\n", e.what());
        return 1;
    }
}
