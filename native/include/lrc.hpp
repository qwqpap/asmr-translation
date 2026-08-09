#pragma once

#include <filesystem>
#include <optional>
#include <string>
#include <vector>

namespace asmr {

struct Cue {
    std::wstring id;
    double start{};
    double end{};
    std::wstring source;
    std::wstring text;
    std::vector<std::wstring> flags;
};

std::vector<Cue> ParseLrc(const std::filesystem::path& path);
std::optional<std::size_t> ActiveCueIndex(const std::vector<Cue>& cues, double seconds);

}  // namespace asmr
