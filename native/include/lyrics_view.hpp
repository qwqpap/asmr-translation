#pragma once

#include "lrc.hpp"

#include <windows.h>
#include <d2d1.h>
#include <dwrite.h>
#include <wrl/client.h>

#include <optional>
#include <utility>
#include <vector>

namespace asmr {

class LyricsView {
public:
    LyricsView() = default;
    ~LyricsView() = default;

    bool Create(HWND parent, HINSTANCE instance, int control_id);
    void SetCues(std::vector<Cue> cues);
    void SetActive(std::optional<std::size_t> active);
    void SetDpi(UINT dpi);
    [[nodiscard]] const std::vector<Cue>& Cues() const noexcept { return cues_; }
    [[nodiscard]] std::optional<std::size_t> Selected() const noexcept { return selected_; }
    [[nodiscard]] HWND Window() const noexcept { return window_; }

private:
    static LRESULT CALLBACK WindowProc(HWND window, UINT message, WPARAM wparam, LPARAM lparam);
    LRESULT HandleMessage(UINT message, WPARAM wparam, LPARAM lparam);
    void EnsureGraphics();
    void Paint();
    std::optional<std::size_t> HitTest(POINT point) const;

    HWND window_{};
    std::vector<Cue> cues_;
    std::optional<std::size_t> active_;
    std::optional<std::size_t> selected_;
    std::vector<std::pair<RECT, std::size_t>> hit_rows_;
    Microsoft::WRL::ComPtr<ID2D1Factory> d2d_factory_;
    Microsoft::WRL::ComPtr<IDWriteFactory> dwrite_factory_;
    Microsoft::WRL::ComPtr<ID2D1HwndRenderTarget> render_target_;
    Microsoft::WRL::ComPtr<ID2D1SolidColorBrush> primary_brush_;
    Microsoft::WRL::ComPtr<ID2D1SolidColorBrush> secondary_brush_;
    Microsoft::WRL::ComPtr<ID2D1SolidColorBrush> active_brush_;
    Microsoft::WRL::ComPtr<ID2D1SolidColorBrush> issue_brush_;
    Microsoft::WRL::ComPtr<IDWriteTextFormat> normal_format_;
    Microsoft::WRL::ComPtr<IDWriteTextFormat> active_format_;
    Microsoft::WRL::ComPtr<IDWriteTextFormat> source_format_;
    Microsoft::WRL::ComPtr<IDWriteTextFormat> issue_format_;
    UINT dpi_{96};
};

}  // namespace asmr
