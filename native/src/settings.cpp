#include "settings.hpp"

#include "utf.hpp"

#include <windows.h>
#include <wincred.h>
#include <shlobj.h>
#include <winrt/Windows.Data.Json.h>
#include <winrt/Windows.Foundation.Collections.h>
#include <winrt/base.h>

#include <fstream>
#include <iterator>
#include <stdexcept>
#include <vector>

namespace asmr {
namespace {

using winrt::Windows::Data::Json::JsonObject;
using winrt::Windows::Data::Json::JsonValue;

std::filesystem::path ApplicationDataDirectory() {
    PWSTR raw = nullptr;
    if (FAILED(SHGetKnownFolderPath(FOLDERID_LocalAppData, KF_FLAG_CREATE, nullptr, &raw))) {
        throw std::runtime_error("cannot locate LocalAppData");
    }
    const std::filesystem::path directory = std::filesystem::path(raw) / L"ASMR Translation";
    CoTaskMemFree(raw);
    std::filesystem::create_directories(directory);
    return directory;
}

std::wstring DefaultDownloadRoot() {
    PWSTR raw = nullptr;
    if (SUCCEEDED(SHGetKnownFolderPath(FOLDERID_Downloads, KF_FLAG_DEFAULT, nullptr, &raw))) {
        const std::filesystem::path path = std::filesystem::path(raw) / L"ASMR Translation";
        CoTaskMemFree(raw);
        return path.wstring();
    }
    return (std::filesystem::current_path() / L"Downloads" / L"ASMR Translation").wstring();
}

std::wstring FindOnPath(const wchar_t* executable) {
    const auto required = SearchPathW(nullptr, executable, nullptr, 0, nullptr, nullptr);
    if (required == 0) {
        return executable;
    }
    std::vector<wchar_t> buffer(static_cast<std::size_t>(required) + 1);
    const auto length = SearchPathW(
        nullptr, executable, nullptr, static_cast<DWORD>(buffer.size()), buffer.data(), nullptr);
    return length > 0 && length < buffer.size() ? std::wstring(buffer.data(), length)
                                                : std::wstring(executable);
}

std::filesystem::path ProjectRootFromPython(const std::wstring& python_path) {
    const std::filesystem::path path(python_path);
    if (!std::filesystem::is_regular_file(path)) {
        return std::filesystem::current_path();
    }
    const auto scripts = path.parent_path();
    const auto environment = scripts.parent_path();
    if (_wcsicmp(scripts.filename().c_str(), L"Scripts") == 0 &&
        _wcsicmp(environment.filename().c_str(), L".venv") == 0) {
        return environment.parent_path();
    }
    return std::filesystem::current_path();
}

std::wstring FindAsrModel(const std::filesystem::path& project_root) {
    for (const auto& relative : {std::filesystem::path(L"models/faster-whisper-large-v3"),
                                 std::filesystem::path(L"models/large-v3")}) {
        const auto candidate = project_root / relative;
        if (std::filesystem::is_regular_file(candidate / L"model.bin")) {
            return candidate.wstring();
        }
    }
    return L"large-v3";
}

std::wstring StringOr(const JsonObject& object,
                      const wchar_t* name,
                      const std::wstring& fallback) {
    return object.HasKey(name) ? std::wstring(object.GetNamedString(name)) : fallback;
}

bool BoolOr(const JsonObject& object, const wchar_t* name, const bool fallback) {
    return object.HasKey(name) ? object.GetNamedBoolean(name) : fallback;
}

ProviderSettings ParseProvider(const JsonObject& object, const ProviderSettings& fallback) {
    ProviderSettings result = fallback;
    result.kind = StringOr(object, L"kind", fallback.kind);
    result.base_url = StringOr(object, L"base_url", fallback.base_url);
    result.model = StringOr(object, L"model", fallback.model);
    result.strict_schema = BoolOr(object, L"strict_schema", fallback.strict_schema);
    return result;
}

JsonObject ProviderJson(const ProviderSettings& provider) {
    JsonObject object;
    object.SetNamedValue(L"kind", JsonValue::CreateStringValue(provider.kind));
    object.SetNamedValue(L"base_url", JsonValue::CreateStringValue(provider.base_url));
    object.SetNamedValue(L"model", JsonValue::CreateStringValue(provider.model));
    object.SetNamedValue(L"strict_schema", JsonValue::CreateBooleanValue(provider.strict_schema));
    return object;
}

}  // namespace

std::filesystem::path SettingsPath() {
    return ApplicationDataDirectory() / L"settings.json";
}

std::wstring FindPythonInterpreter() {
    std::vector<std::filesystem::path> roots;
    roots.push_back(std::filesystem::current_path());
    std::vector<wchar_t> module(32768);
    const auto length = GetModuleFileNameW(nullptr, module.data(), static_cast<DWORD>(module.size()));
    if (length > 0 && length < module.size()) {
        auto root = std::filesystem::path(std::wstring(module.data(), length)).parent_path();
        for (int level = 0; level < 7 && !root.empty(); ++level) {
            roots.push_back(root);
            root = root.parent_path();
        }
    }
    for (const auto& root : roots) {
        const auto candidate = root / L".venv" / L"Scripts" / L"python.exe";
        if (std::filesystem::is_regular_file(candidate)) {
            return candidate.wstring();
        }
    }
    return L"python";
}

std::wstring FindFfmpegExecutable() {
    return FindOnPath(L"ffmpeg.exe");
}

AppSettings LoadSettings() {
    AppSettings defaults;
    defaults.python_path = FindPythonInterpreter();
    defaults.ffmpeg_path = FindFfmpegExecutable();
    const auto project_root = ProjectRootFromPython(defaults.python_path);
    defaults.asr_model = FindAsrModel(project_root);
    defaults.cache_root = (project_root / L".cache").wstring();
    defaults.download_root = DefaultDownloadRoot();
    defaults.download_endpoint = L"https://api.asmr-200.com";
    defaults.download_connect_timeout = 10;
    const auto glossary = project_root / L"glossary.json";
    if (std::filesystem::is_regular_file(glossary)) {
        defaults.glossary_path = glossary.wstring();
    }
    const auto path = SettingsPath();
    if (!std::filesystem::is_regular_file(path)) {
        return defaults;
    }
    try {
        std::ifstream stream(path, std::ios::binary);
        const std::string bytes((std::istreambuf_iterator<char>(stream)),
                                std::istreambuf_iterator<char>());
        defaults = ParseSettingsUtf8(bytes, std::move(defaults));
    } catch (...) {
        return defaults;
    }
    return defaults;
}

AppSettings ParseSettingsUtf8(const std::string_view json, AppSettings defaults) {
    const auto root = JsonObject::Parse(Utf8ToWide(json));
    defaults.python_path = StringOr(root, L"python_path", defaults.python_path);
    defaults.asr_model = StringOr(root, L"asr_model", defaults.asr_model);
    defaults.ffmpeg_path = StringOr(root, L"ffmpeg_path", defaults.ffmpeg_path);
    defaults.cache_root = StringOr(root, L"cache_root", defaults.cache_root);
    defaults.glossary_path = StringOr(root, L"glossary_path", defaults.glossary_path);
    defaults.download_root = StringOr(root, L"download_root", defaults.download_root);
    defaults.download_endpoint =
        StringOr(root, L"download_endpoint", defaults.download_endpoint);
    defaults.curl_path = StringOr(root, L"curl_path", defaults.curl_path);
    defaults.download_proxy = StringOr(root, L"download_proxy", defaults.download_proxy);
    if (root.HasKey(L"download_connect_timeout")) {
        defaults.download_connect_timeout = static_cast<int>(
            root.GetNamedNumber(L"download_connect_timeout", defaults.download_connect_timeout));
        if (defaults.download_connect_timeout <= 0) {
            defaults.download_connect_timeout = 10;
        }
    }
    defaults.download_notice_shown =
        BoolOr(root, L"download_notice_shown", defaults.download_notice_shown);
    defaults.review_same_as_draft =
        BoolOr(root, L"review_same_as_draft", defaults.review_same_as_draft);
    defaults.quality_mode = BoolOr(root, L"quality_mode", defaults.quality_mode);
    if (root.HasKey(L"draft")) {
        defaults.draft = ParseProvider(root.GetNamedObject(L"draft"), defaults.draft);
    }
    if (root.HasKey(L"review")) {
        defaults.review = ParseProvider(root.GetNamedObject(L"review"), defaults.review);
    }
    return defaults;
}

std::string SerializeSettingsUtf8(const AppSettings& settings) {
    JsonObject root;
    root.SetNamedValue(L"python_path", JsonValue::CreateStringValue(settings.python_path));
    root.SetNamedValue(L"asr_model", JsonValue::CreateStringValue(settings.asr_model));
    root.SetNamedValue(L"ffmpeg_path", JsonValue::CreateStringValue(settings.ffmpeg_path));
    root.SetNamedValue(L"cache_root", JsonValue::CreateStringValue(settings.cache_root));
    root.SetNamedValue(L"glossary_path", JsonValue::CreateStringValue(settings.glossary_path));
    root.SetNamedValue(L"download_root", JsonValue::CreateStringValue(settings.download_root));
    root.SetNamedValue(L"download_endpoint",
                       JsonValue::CreateStringValue(settings.download_endpoint));
    root.SetNamedValue(L"curl_path", JsonValue::CreateStringValue(settings.curl_path));
    root.SetNamedValue(L"download_proxy",
                       JsonValue::CreateStringValue(settings.download_proxy));
    root.SetNamedValue(L"download_connect_timeout",
                       JsonValue::CreateNumberValue(settings.download_connect_timeout));
    root.SetNamedValue(L"download_notice_shown",
                       JsonValue::CreateBooleanValue(settings.download_notice_shown));
    root.SetNamedValue(L"review_same_as_draft",
                       JsonValue::CreateBooleanValue(settings.review_same_as_draft));
    root.SetNamedValue(L"quality_mode", JsonValue::CreateBooleanValue(settings.quality_mode));
    root.SetNamedValue(L"draft", ProviderJson(settings.draft));
    root.SetNamedValue(L"review", ProviderJson(settings.review));
    return WideToUtf8(std::wstring(root.Stringify()));
}

void SaveSettings(const AppSettings& settings) {
    const auto bytes = SerializeSettingsUtf8(settings);
    const auto path = SettingsPath();
    std::filesystem::create_directories(path.parent_path());
    const auto temporary = path.wstring() + L".tmp";
    {
        std::ofstream stream(temporary, std::ios::binary | std::ios::trunc);
        stream.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
        stream.flush();
        if (!stream) {
            throw std::runtime_error("cannot write settings");
        }
    }
    if (!MoveFileExW(temporary.c_str(),
                     path.c_str(),
                     MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        DeleteFileW(temporary.c_str());
        throw std::runtime_error("cannot replace settings");
    }
}

std::wstring ReadCredential(const std::wstring& target) {
    PCREDENTIALW credential = nullptr;
    if (!CredReadW(target.c_str(), CRED_TYPE_GENERIC, 0, &credential)) {
        return {};
    }
    const auto characters = credential->CredentialBlobSize / sizeof(wchar_t);
    const std::wstring result(reinterpret_cast<const wchar_t*>(credential->CredentialBlob),
                              characters);
    CredFree(credential);
    return result;
}

void WriteCredential(const std::wstring& target, const std::wstring& secret) {
    if (secret.empty()) {
        CredDeleteW(target.c_str(), CRED_TYPE_GENERIC, 0);
        return;
    }
    CREDENTIALW credential{};
    credential.Type = CRED_TYPE_GENERIC;
    credential.TargetName = const_cast<wchar_t*>(target.c_str());
    credential.CredentialBlobSize = static_cast<DWORD>(secret.size() * sizeof(wchar_t));
    credential.CredentialBlob = reinterpret_cast<LPBYTE>(const_cast<wchar_t*>(secret.data()));
    credential.Persist = CRED_PERSIST_LOCAL_MACHINE;
    credential.UserName = const_cast<wchar_t*>(L"ASMR Translation");
    if (!CredWriteW(&credential, 0)) {
        throw std::runtime_error("cannot write Windows credential");
    }
}

}  // namespace asmr
