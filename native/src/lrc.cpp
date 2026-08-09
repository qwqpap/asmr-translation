#include "lrc.hpp"

#include "utf.hpp"

#include <algorithm>
#include <fstream>
#include <limits>
#include <regex>
#include <sstream>
#include <stdexcept>

namespace asmr {

std::vector<Cue> ParseLrc(const std::filesystem::path& path) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) {
        return {};
    }
    std::string bytes((std::istreambuf_iterator<char>(stream)), std::istreambuf_iterator<char>());
    if (bytes.starts_with("\xEF\xBB\xBF")) {
        bytes.erase(0, 3);
    }
    const auto content = Utf8ToWide(bytes);
    std::wstringstream lines(content);
    const std::wregex pattern(LR"(^\[(\d+):(\d{2})\.(\d{2})\](.+)$)");
    std::vector<Cue> cues;
    std::wstring line;
    std::size_t line_number = 0;
    while (std::getline(lines, line)) {
        ++line_number;
        if (!line.empty() && line.back() == L'\r') {
            line.pop_back();
        }
        std::wsmatch match;
        if (!std::regex_match(line, match, pattern)) {
            continue;
        }
        const auto seconds = std::stod(match[1].str()) * 60.0 + std::stod(match[2].str()) +
                             std::stod(match[3].str()) / 100.0;
        Cue cue;
        cue.id = L"lrc-" + std::to_wstring(line_number);
        cue.start = seconds;
        cue.end = std::numeric_limits<double>::infinity();
        cue.text = match[4].str();
        cues.push_back(std::move(cue));
    }
    std::ranges::stable_sort(cues, {}, &Cue::start);
    for (std::size_t index = 0; index + 1 < cues.size(); ++index) {
        cues[index].end = cues[index + 1].start;
    }
    return cues;
}

std::optional<std::size_t> ActiveCueIndex(const std::vector<Cue>& cues, const double seconds) {
    if (cues.empty() || seconds < cues.front().start) {
        return std::nullopt;
    }
    const auto found = std::upper_bound(
        cues.begin(), cues.end(), seconds, [](const double value, const Cue& cue) {
            return value < cue.start;
        });
    return static_cast<std::size_t>(std::distance(cues.begin(), found) - 1);
}

}  // namespace asmr
