#include "utf.hpp"

#include <windows.h>
#include <shlwapi.h>

#include <stdexcept>
#include <vector>

namespace asmr {

std::wstring Utf8ToWide(const std::string_view value) {
    if (value.empty()) {
        return {};
    }
    const auto size = MultiByteToWideChar(
        CP_UTF8, MB_ERR_INVALID_CHARS, value.data(), static_cast<int>(value.size()), nullptr, 0);
    if (size <= 0) {
        throw std::runtime_error("invalid UTF-8 text");
    }
    std::wstring result(static_cast<std::size_t>(size), L'\0');
    MultiByteToWideChar(CP_UTF8,
                        MB_ERR_INVALID_CHARS,
                        value.data(),
                        static_cast<int>(value.size()),
                        result.data(),
                        size);
    return result;
}

std::string WideToUtf8(const std::wstring_view value) {
    if (value.empty()) {
        return {};
    }
    const auto size = WideCharToMultiByte(CP_UTF8,
                                          WC_ERR_INVALID_CHARS,
                                          value.data(),
                                          static_cast<int>(value.size()),
                                          nullptr,
                                          0,
                                          nullptr,
                                          nullptr);
    if (size <= 0) {
        throw std::runtime_error("invalid UTF-16 text");
    }
    std::string result(static_cast<std::size_t>(size), '\0');
    WideCharToMultiByte(CP_UTF8,
                        WC_ERR_INVALID_CHARS,
                        value.data(),
                        static_cast<int>(value.size()),
                        result.data(),
                        size,
                        nullptr,
                        nullptr);
    return result;
}

std::wstring FilePathToUri(const std::filesystem::path& path) {
    const auto absolute = std::filesystem::absolute(path).wstring();
    // UrlCreateFromPathW does not reliably support a null-buffer size probe.
    // A URI may percent-encode every UTF-16 code unit, so reserve three times
    // the path length and still honor E_POINTER if Windows requests more.
    std::vector<wchar_t> buffer(absolute.size() * 3 + 32);
    DWORD length = static_cast<DWORD>(buffer.size());
    auto result = UrlCreateFromPathW(absolute.c_str(), buffer.data(), &length, 0);
    if (result == E_POINTER) {
        buffer.resize(static_cast<std::size_t>(length) + 1);
        length = static_cast<DWORD>(buffer.size());
        result = UrlCreateFromPathW(absolute.c_str(), buffer.data(), &length, 0);
    }
    if (FAILED(result)) {
        throw std::runtime_error("failed to convert path to file URI");
    }
    return std::wstring(buffer.data());
}

}  // namespace asmr
