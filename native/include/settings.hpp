#pragma once

#include <filesystem>
#include <string>
#include <string_view>

namespace asmr {

struct ProviderSettings {
    std::wstring kind = L"ollama";
    std::wstring base_url = L"http://127.0.0.1:11434";
    std::wstring model = L"qwen3.5-9b-abliterated:latest";
    bool strict_schema = true;
};

struct AppSettings {
    std::wstring python_path;
    std::wstring ffmpeg_path;
    std::wstring cache_root;
    std::wstring glossary_path;
    ProviderSettings draft;
    ProviderSettings review;
    bool review_same_as_draft = true;
    bool quality_mode = true;
};

std::filesystem::path SettingsPath();
std::wstring FindPythonInterpreter();
std::wstring FindFfmpegExecutable();
AppSettings LoadSettings();
void SaveSettings(const AppSettings& settings);
AppSettings ParseSettingsUtf8(std::string_view json, AppSettings defaults = {});
std::string SerializeSettingsUtf8(const AppSettings& settings);

std::wstring ReadCredential(const std::wstring& target);
void WriteCredential(const std::wstring& target, const std::wstring& secret);

}  // namespace asmr
