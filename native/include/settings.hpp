#pragma once

#include <filesystem>
#include <string>
#include <string_view>

namespace asmr {

struct ProviderSettings {
    std::wstring kind = L"ollama";
    std::wstring base_url = L"http://127.0.0.1:11434";
    std::wstring model = L"translategemma:4b";
    bool strict_schema = true;
    std::wstring protocol = L"chat-json";
};

struct AppSettings {
    std::wstring python_path;
    std::wstring asr_model;
    std::wstring ffmpeg_path;
    std::wstring cache_root;
    std::wstring glossary_path;
    std::wstring download_root;
    std::wstring download_endpoint = L"https://api.asmr-200.com";
    std::wstring curl_path;
    std::wstring download_proxy;
    int download_connect_timeout = 10;
    bool download_notice_shown = false;
    bool setup_prompted = false;
    bool setup_completed = false;
    ProviderSettings draft;
    ProviderSettings review;
    ProviderSettings analysis;
    ProviderSettings fallback;
    bool analysis_enabled = true;
    bool fallback_enabled = true;
    bool review_same_as_draft = true;
    bool review_enabled = false;
    bool quality_mode = true;
};

std::filesystem::path SettingsPath();
std::filesystem::path ApplicationInstallDirectory();
std::filesystem::path ApplicationDataDirectory();
std::filesystem::path BootstrapScriptPath();
std::filesystem::path BootstrapManifestPath();
std::filesystem::path EmbeddedRuntimeRoot();
std::wstring FindPythonInterpreter();
std::wstring FindFfmpegExecutable();
bool IsEmbeddedPython(const std::wstring& path);
AppSettings LoadSettings();
void SaveSettings(const AppSettings& settings);
AppSettings ParseSettingsUtf8(std::string_view json, AppSettings defaults = {});
std::string SerializeSettingsUtf8(const AppSettings& settings);

std::wstring ReadCredential(const std::wstring& target);
void WriteCredential(const std::wstring& target, const std::wstring& secret);

}  // namespace asmr
