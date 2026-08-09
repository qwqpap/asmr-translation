#pragma once

#include <windows.h>
#include <mfmediaengine.h>
#include <wrl/client.h>

#include <filesystem>

namespace asmr {

class MediaPlayer {
public:
    MediaPlayer() = default;
    ~MediaPlayer();

    MediaPlayer(const MediaPlayer&) = delete;
    MediaPlayer& operator=(const MediaPlayer&) = delete;

    bool Initialize(HWND notify_window);
    bool Open(const std::filesystem::path& path);
    void Close();
    void PlayPause();
    void Seek(double seconds);
    void SetVolume(double volume);
    void SetRate(double rate);

    [[nodiscard]] double Position() const;
    [[nodiscard]] double Duration() const;
    [[nodiscard]] bool Paused() const;
    [[nodiscard]] bool Ready() const noexcept { return ready_; }
    [[nodiscard]] bool SourceUnsupported() const noexcept { return source_unsupported_; }
    [[nodiscard]] DWORD ErrorCode() const noexcept { return error_code_; }
    [[nodiscard]] HRESULT ExtendedError() const noexcept { return extended_error_; }
    [[nodiscard]] HRESULT OpenError() const noexcept { return open_error_; }
    [[nodiscard]] const std::filesystem::path& Source() const noexcept { return source_; }

    void HandleEvent(DWORD event_code);

private:
    Microsoft::WRL::ComPtr<IMFMediaEngine> engine_;
    bool media_foundation_started_{};
    bool ready_{};
    bool source_unsupported_{};
    DWORD error_code_{};
    HRESULT extended_error_{S_OK};
    HRESULT open_error_{S_OK};
    std::filesystem::path source_;
};

}  // namespace asmr
