#include "app_messages.hpp"
#include "lrc.hpp"
#include "media_player.hpp"
#include "page_host.hpp"
#include "settings.hpp"
#include "utf.hpp"
#include "worker_protocol.hpp"

#include <windows.h>
#include <commctrl.h>
#include <winrt/base.h>

#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <thread>
#include <vector>

namespace {

int failures = 0;
asmr::MediaPlayer* media_player = nullptr;
UINT forwarded_message = 0;
WPARAM forwarded_wparam = 0;

void Check(const bool condition, const char* message) {
    if (!condition) {
        std::cerr << "FAIL: " << message << '\n';
        ++failures;
    }
}

LRESULT CALLBACK MediaWindowProcedure(const HWND window,
                                      const UINT message,
                                      const WPARAM wparam,
                                      const LPARAM lparam) {
    if (message == WM_APP_MEDIA_EVENT && media_player != nullptr) {
        media_player->HandleEvent(static_cast<DWORD>(wparam));
        return 0;
    }
    if (message == WM_COMMAND || message == WM_NOTIFY || message == WM_HSCROLL ||
        message == WM_APP_LYRIC_CLICK) {
        forwarded_message = message;
        forwarded_wparam = wparam;
        return 0;
    }
    return DefWindowProcW(window, message, wparam, lparam);
}

void WritePcmWav(const std::filesystem::path& path) {
    constexpr std::uint32_t sample_rate = 8000;
    constexpr std::uint16_t channels = 1;
    constexpr std::uint16_t bits_per_sample = 16;
    constexpr std::uint32_t sample_count = sample_rate / 4;
    constexpr std::uint32_t data_bytes = sample_count * channels * bits_per_sample / 8;
    constexpr std::uint32_t byte_rate = sample_rate * channels * bits_per_sample / 8;
    constexpr std::uint16_t block_align = channels * bits_per_sample / 8;

    std::ofstream stream(path, std::ios::binary | std::ios::trunc);
    const auto write_u16 = [&stream](const std::uint16_t value) {
        stream.write(reinterpret_cast<const char*>(&value), sizeof(value));
    };
    const auto write_u32 = [&stream](const std::uint32_t value) {
        stream.write(reinterpret_cast<const char*>(&value), sizeof(value));
    };
    stream.write("RIFF", 4);
    write_u32(36 + data_bytes);
    stream.write("WAVEfmt ", 8);
    write_u32(16);
    write_u16(1);
    write_u16(channels);
    write_u32(sample_rate);
    write_u32(byte_rate);
    write_u16(block_align);
    write_u16(bits_per_sample);
    stream.write("data", 4);
    write_u32(data_bytes);
    const std::vector<char> silence(data_bytes, 0);
    stream.write(silence.data(), static_cast<std::streamsize>(silence.size()));
}

bool WaitForMediaResult(asmr::MediaPlayer& player) {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
    while (std::chrono::steady_clock::now() < deadline && !player.Ready() &&
           !player.SourceUnsupported()) {
        MSG message{};
        while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) {
            TranslateMessage(&message);
            DispatchMessageW(&message);
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    return player.Ready();
}

std::filesystem::path OptionalRealMedia() {
    const auto required = GetEnvironmentVariableW(L"ASMR_MEDIA_TEST_FILE", nullptr, 0);
    if (required == 0) {
        return {};
    }
    std::wstring value(required, L'\0');
    const auto length = GetEnvironmentVariableW(
        L"ASMR_MEDIA_TEST_FILE", value.data(), static_cast<DWORD>(value.size()));
    value.resize(length);
    return std::filesystem::path(value);
}

void CheckMediaFile(asmr::MediaPlayer& player,
                    const std::filesystem::path& path,
                    const char* label) {
    const auto opened = player.Open(path);
    if (!opened) {
        std::cerr << "FAIL: " << label << " immediate open HRESULT 0x" << std::hex
                  << static_cast<unsigned long>(player.OpenError()) << std::dec << '\n';
        ++failures;
        return;
    }
    if (!WaitForMediaResult(player)) {
        std::cerr << "FAIL: " << label << " asynchronous media error " << player.ErrorCode()
                  << ", HRESULT 0x" << std::hex
                  << static_cast<unsigned long>(player.ExtendedError()) << std::dec << '\n';
        ++failures;
    }
}

}  // namespace

int wmain() {
    winrt::init_apartment(winrt::apartment_type::single_threaded);
    const auto sample = std::wstring(L"日语 ASMR / 中文台词");
    Check(asmr::Utf8ToWide(asmr::WideToUtf8(sample)) == sample, "UTF round trip");
    const auto unicode_uri = asmr::FilePathToUri(
        std::filesystem::temp_directory_path() / L"中文 音频.wav");
    Check(unicode_uri.starts_with(L"file:"), "Unicode path converts to file URI");

    const auto path = std::filesystem::temp_directory_path() /
                      (L"asmr-native-test-" + std::to_wstring(GetCurrentProcessId()) + L".lrc");
    {
        std::ofstream stream(path, std::ios::binary | std::ios::trunc);
        stream << "\xEF\xBB\xBF[00:02.50]第二行 A\n"
                  "[00:01.00]第一行\n"
                  "[00:02.50]第二行 B\n"
                  "[invalid]忽略\n";
    }
    const auto cues = asmr::ParseLrc(path);
    std::filesystem::remove(path);
    Check(cues.size() == 3, "parse UTF-8 BOM and three cues");
    Check(cues[0].text == L"第一行", "stable timestamp sort");
    Check(!asmr::ActiveCueIndex(cues, 0.5).has_value(), "no cue before first timestamp");
    Check(asmr::ActiveCueIndex(cues, 1.2).value_or(99) == 0, "first active cue");
    Check(asmr::ActiveCueIndex(cues, 2.49).value_or(99) == 0, "last cue not later");
    Check(asmr::ActiveCueIndex(cues, 2.5).value_or(99) == 2,
          "last duplicate timestamp wins");

    asmr::AppSettings settings;
    settings.python_path = L"C:\\资料库\\.venv\\Scripts\\python.exe";
    settings.ffmpeg_path = L"C:\\工具\\ffmpeg.exe";
    settings.cache_root = L"C:\\很长的中文路径\\缓存";
    settings.glossary_path = L"C:\\资料库\\固定术语.json";
    settings.draft.kind = L"openai";
    settings.draft.base_url = L"https://example.test/v1";
    settings.draft.model = L"draft-model";
    settings.review_same_as_draft = false;
    const auto settings_json = asmr::SerializeSettingsUtf8(settings);
    const auto loaded = asmr::ParseSettingsUtf8(settings_json);
    Check(loaded.python_path == settings.python_path, "settings Unicode round trip");
    Check(loaded.glossary_path == settings.glossary_path, "settings glossary path");
    Check(loaded.draft.model == settings.draft.model, "settings provider model");
    Check(settings_json.find("api_key") == std::string::npos,
          "settings never serialize API key");

    const auto event = asmr::ParseWorkerEventEnvelope(
        L"{\"protocol\":1,\"event\":\"cues\",\"message\":\"中文\"}\n");
    Check(event.protocol == 1 && event.event == L"cues", "parse UTF-8 JSONL envelope");
    bool rejected_protocol = false;
    try {
        (void)asmr::ParseWorkerEventEnvelope(L"{\"protocol\":2,\"event\":\"log\"}");
    } catch (const std::exception&) {
        rejected_protocol = true;
    }
    Check(rejected_protocol, "reject unknown worker protocol");

    const wchar_t* media_window_class = L"AsmrTranslationNativeTestWindow";
    WNDCLASSW window_class{};
    window_class.lpfnWndProc = MediaWindowProcedure;
    window_class.hInstance = GetModuleHandleW(nullptr);
    window_class.lpszClassName = media_window_class;
    Check(RegisterClassW(&window_class) != 0, "register media test window");
    const auto media_window = CreateWindowExW(0,
                                               media_window_class,
                                               L"",
                                               0,
                                               0,
                                               0,
                                               0,
                                               0,
                                               HWND_MESSAGE,
                                               nullptr,
                                               window_class.hInstance,
                                               nullptr);
    Check(media_window != nullptr, "create media test window");
    if (media_window != nullptr) {
        const auto page = asmr::CreatePageHost(media_window, GetModuleHandleW(nullptr));
        Check(page != nullptr, "create page host");
        if (page != nullptr) {
            forwarded_message = 0;
            forwarded_wparam = 0;
            SendMessageW(page, WM_COMMAND, MAKEWPARAM(4242, BN_CLICKED), 0);
            Check(forwarded_message == WM_COMMAND && LOWORD(forwarded_wparam) == 4242,
                  "page host forwards button commands to root window");

            forwarded_message = 0;
            SendMessageW(page, WM_HSCROLL, TB_THUMBPOSITION, 0);
            Check(forwarded_message == WM_HSCROLL,
                  "page host forwards trackbar notifications to root window");

            forwarded_message = 0;
            SendMessageW(page, WM_APP_LYRIC_CLICK, 7, 0);
            Check(forwarded_message == WM_APP_LYRIC_CLICK && forwarded_wparam == 7,
                  "page host forwards lyric actions to root window");
            DestroyWindow(page);
        }
        asmr::MediaPlayer player;
        media_player = &player;
        Check(player.Initialize(media_window), "initialize Media Foundation player");
        const auto wav = std::filesystem::temp_directory_path() /
                         (L"asmr-native-media-测试-" +
                          std::to_wstring(GetCurrentProcessId()) + L".wav");
        WritePcmWav(wav);
        CheckMediaFile(player, wav, "generated PCM WAV");
        std::filesystem::remove(wav);
        const auto real_media = OptionalRealMedia();
        if (!real_media.empty()) {
            Check(std::filesystem::is_regular_file(real_media), "real media fixture exists");
            if (std::filesystem::is_regular_file(real_media)) {
                CheckMediaFile(player, real_media, "real media fixture");
            }
        }
        media_player = nullptr;
        DestroyWindow(media_window);
    }
    return failures == 0 ? 0 : 1;
}
