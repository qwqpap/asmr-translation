#pragma once

#include <filesystem>
#include <string>
#include <string_view>

namespace asmr {

std::wstring Utf8ToWide(std::string_view value);
std::string WideToUtf8(std::wstring_view value);
std::wstring FilePathToUri(const std::filesystem::path& path);

}  // namespace asmr
