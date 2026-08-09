#include "lyrics_view.hpp"

#include "app_messages.hpp"

#include <windowsx.h>

#include <algorithm>
#include <cmath>
#include <mutex>

namespace asmr {
namespace {

constexpr wchar_t kLyricsClass[] = L"ASMRTranslationLyricsView";
std::once_flag register_flag;

D2D1_RECT_F ToRectF(const RECT& value) {
    return D2D1::RectF(static_cast<float>(value.left),
                       static_cast<float>(value.top),
                       static_cast<float>(value.right),
                       static_cast<float>(value.bottom));
}

std::wstring FlagLabel(const std::vector<std::wstring>& flags) {
    std::wstring result;
    for (const auto& flag : flags) {
        const wchar_t* label = nullptr;
        if (flag == L"review_changed") {
            label = L"审校修正";
        } else if (flag == L"asr_suspect") {
            label = L"疑似 ASR";
        } else if (flag == L"term_conflict") {
            label = L"术语冲突";
        } else if (flag == L"low_confidence") {
            label = L"低置信";
        } else if (flag == L"manual_edited") {
            label = L"人工修改";
        }
        if (label != nullptr) {
            if (!result.empty()) {
                result += L" · ";
            }
            result += label;
        }
    }
    return result;
}

}  // namespace

bool LyricsView::Create(const HWND parent, const HINSTANCE instance, const int control_id) {
    std::call_once(register_flag, [instance] {
        WNDCLASSEXW window_class{sizeof(WNDCLASSEXW)};
        window_class.lpfnWndProc = WindowProc;
        window_class.hInstance = instance;
        window_class.hCursor = LoadCursorW(nullptr, IDC_HAND);
        window_class.style = CS_DBLCLKS;
        window_class.lpszClassName = kLyricsClass;
        RegisterClassExW(&window_class);
    });
    window_ = CreateWindowExW(WS_EX_CLIENTEDGE,
                              kLyricsClass,
                              L"",
                              WS_CHILD | WS_VISIBLE | WS_TABSTOP,
                              0,
                              0,
                              100,
                              100,
                              parent,
                              reinterpret_cast<HMENU>(static_cast<INT_PTR>(control_id)),
                              instance,
                              this);
    return window_ != nullptr;
}

void LyricsView::SetCues(std::vector<Cue> cues) {
    cues_ = std::move(cues);
    active_.reset();
    selected_.reset();
    InvalidateRect(window_, nullptr, FALSE);
}

void LyricsView::SetActive(const std::optional<std::size_t> active) {
    if (active_ == active) {
        return;
    }
    active_ = active;
    InvalidateRect(window_, nullptr, FALSE);
}

void LyricsView::SetDpi(const UINT dpi) {
    if (dpi_ == dpi || dpi == 0) {
        return;
    }
    dpi_ = dpi;
    render_target_.Reset();
    primary_brush_.Reset();
    secondary_brush_.Reset();
    active_brush_.Reset();
    issue_brush_.Reset();
    normal_format_.Reset();
    active_format_.Reset();
    source_format_.Reset();
    issue_format_.Reset();
    InvalidateRect(window_, nullptr, FALSE);
}

LRESULT CALLBACK LyricsView::WindowProc(const HWND window,
                                        const UINT message,
                                        const WPARAM wparam,
                                        const LPARAM lparam) {
    LyricsView* self = nullptr;
    if (message == WM_NCCREATE) {
        const auto* create = reinterpret_cast<CREATESTRUCTW*>(lparam);
        self = static_cast<LyricsView*>(create->lpCreateParams);
        self->window_ = window;
        SetWindowLongPtrW(window, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(self));
    } else {
        self = reinterpret_cast<LyricsView*>(GetWindowLongPtrW(window, GWLP_USERDATA));
    }
    return self != nullptr ? self->HandleMessage(message, wparam, lparam)
                           : DefWindowProcW(window, message, wparam, lparam);
}

LRESULT LyricsView::HandleMessage(const UINT message, const WPARAM wparam, const LPARAM lparam) {
    switch (message) {
        case WM_PAINT:
            Paint();
            return 0;
        case WM_ERASEBKGND:
            return 1;
        case WM_SIZE:
            if (render_target_) {
                render_target_->Resize(D2D1::SizeU(LOWORD(lparam), HIWORD(lparam)));
            }
            return 0;
        case WM_LBUTTONDOWN: {
            const POINT point{GET_X_LPARAM(lparam), GET_Y_LPARAM(lparam)};
            selected_ = HitTest(point);
            if (selected_) {
                SendMessageW(GetParent(window_), WM_APP_LYRIC_CLICK, *selected_, 0);
            }
            return 0;
        }
        case WM_LBUTTONDBLCLK: {
            const POINT point{GET_X_LPARAM(lparam), GET_Y_LPARAM(lparam)};
            selected_ = HitTest(point);
            if (selected_) {
                SendMessageW(GetParent(window_), WM_APP_LYRIC_EDIT, *selected_, 0);
            }
            return 0;
        }
        case WM_DESTROY:
            render_target_.Reset();
            primary_brush_.Reset();
            secondary_brush_.Reset();
            active_brush_.Reset();
            issue_brush_.Reset();
            return 0;
        default:
            return DefWindowProcW(window_, message, wparam, lparam);
    }
}

void LyricsView::EnsureGraphics() {
    const auto scale = [this](const float value) {
        return value * static_cast<float>(dpi_) / 96.0F;
    };
    if (!d2d_factory_) {
        D2D1CreateFactory(D2D1_FACTORY_TYPE_SINGLE_THREADED,
                          d2d_factory_.ReleaseAndGetAddressOf());
    }
    if (!dwrite_factory_) {
        DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED,
                            __uuidof(IDWriteFactory),
                            reinterpret_cast<IUnknown**>(dwrite_factory_.GetAddressOf()));
        dwrite_factory_->CreateTextFormat(L"Microsoft YaHei UI",
                                          nullptr,
                                          DWRITE_FONT_WEIGHT_NORMAL,
                                          DWRITE_FONT_STYLE_NORMAL,
                                          DWRITE_FONT_STRETCH_NORMAL,
                                          scale(20.0F),
                                          L"zh-CN",
                                          &normal_format_);
        dwrite_factory_->CreateTextFormat(L"Microsoft YaHei UI",
                                          nullptr,
                                          DWRITE_FONT_WEIGHT_SEMI_BOLD,
                                          DWRITE_FONT_STYLE_NORMAL,
                                          DWRITE_FONT_STRETCH_NORMAL,
                                          scale(30.0F),
                                          L"zh-CN",
                                          &active_format_);
        dwrite_factory_->CreateTextFormat(L"Yu Gothic UI",
                                          nullptr,
                                          DWRITE_FONT_WEIGHT_NORMAL,
                                          DWRITE_FONT_STYLE_NORMAL,
                                          DWRITE_FONT_STRETCH_NORMAL,
                                          scale(15.0F),
                                          L"ja-JP",
                                          &source_format_);
        dwrite_factory_->CreateTextFormat(L"Microsoft YaHei UI",
                                          nullptr,
                                          DWRITE_FONT_WEIGHT_SEMI_BOLD,
                                          DWRITE_FONT_STYLE_NORMAL,
                                          DWRITE_FONT_STRETCH_NORMAL,
                                          scale(11.0F),
                                          L"zh-CN",
                                          &issue_format_);
        for (auto* format : {normal_format_.Get(),
                             active_format_.Get(),
                             source_format_.Get(),
                             issue_format_.Get()}) {
            format->SetTextAlignment(DWRITE_TEXT_ALIGNMENT_CENTER);
            format->SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_CENTER);
            format->SetWordWrapping(DWRITE_WORD_WRAPPING_WRAP);
        }
    }
    if (!render_target_) {
        RECT client{};
        GetClientRect(window_, &client);
        d2d_factory_->CreateHwndRenderTarget(
            D2D1::RenderTargetProperties(),
            D2D1::HwndRenderTargetProperties(
                window_, D2D1::SizeU(client.right - client.left, client.bottom - client.top)),
            &render_target_);
        render_target_->SetDpi(96.0F, 96.0F);
        render_target_->CreateSolidColorBrush(
            D2D1::ColorF(0xD8DEE9), &primary_brush_);
        render_target_->CreateSolidColorBrush(
            D2D1::ColorF(0x7F8EA3), &secondary_brush_);
        render_target_->CreateSolidColorBrush(
            D2D1::ColorF(0x88C0D0), &active_brush_);
        render_target_->CreateSolidColorBrush(
            D2D1::ColorF(0xEBCB8B), &issue_brush_);
    }
}

void LyricsView::Paint() {
    PAINTSTRUCT paint{};
    BeginPaint(window_, &paint);
    EnsureGraphics();
    if (!render_target_) {
        EndPaint(window_, &paint);
        return;
    }
    render_target_->BeginDraw();
    render_target_->Clear(D2D1::ColorF(0x151A22));
    hit_rows_.clear();
    RECT client{};
    GetClientRect(window_, &client);
    const auto scale = [this](const int value) {
        return MulDiv(value, static_cast<int>(dpi_), 96);
    };
    if (cues_.empty()) {
        const std::wstring placeholder = L"打开音频与同名 LRC 后，台词会在这里同步显示";
        render_target_->DrawTextW(placeholder.c_str(),
                                  static_cast<UINT32>(placeholder.size()),
                                  normal_format_.Get(),
                                  ToRectF(client),
                                  secondary_brush_.Get());
    } else {
        const auto center = active_.value_or(selected_.value_or(0));
        const auto height = client.bottom - client.top;
        const auto middle = height / 2;
        for (int offset = -3; offset <= 3; ++offset) {
            const auto signed_index = static_cast<long long>(center) + offset;
            if (signed_index < 0 || signed_index >= static_cast<long long>(cues_.size())) {
                continue;
            }
            const auto index = static_cast<std::size_t>(signed_index);
            const bool active = index == active_;
            const int row_height = scale(active ? 104 : 72);
            const int top = middle + offset * scale(78) - row_height / 2;
            RECT row{client.left + scale(24),
                     top,
                     client.right - scale(24),
                     top + row_height};
            hit_rows_.emplace_back(row, index);
            RECT translation = row;
            translation.bottom = row.top + scale(active ? 56 : 36);
            RECT source = row;
            source.top = translation.bottom;
            const auto flags = FlagLabel(cues_[index].flags);
            RECT issue = row;
            if (!flags.empty()) {
                issue.top = row.bottom - scale(18);
                source.bottom = issue.top;
            }
            render_target_->DrawTextW(cues_[index].text.c_str(),
                                      static_cast<UINT32>(cues_[index].text.size()),
                                      active ? active_format_.Get() : normal_format_.Get(),
                                      ToRectF(translation),
                                      active ? active_brush_.Get() : primary_brush_.Get());
            if (!cues_[index].source.empty()) {
                render_target_->DrawTextW(cues_[index].source.c_str(),
                                          static_cast<UINT32>(cues_[index].source.size()),
                                          source_format_.Get(),
                                          ToRectF(source),
                                          secondary_brush_.Get());
            }
            if (!flags.empty()) {
                render_target_->DrawTextW(flags.c_str(),
                                          static_cast<UINT32>(flags.size()),
                                          issue_format_.Get(),
                                          ToRectF(issue),
                                          issue_brush_.Get());
            }
        }
    }
    const auto result = render_target_->EndDraw();
    if (result == D2DERR_RECREATE_TARGET) {
        render_target_.Reset();
        primary_brush_.Reset();
        secondary_brush_.Reset();
        active_brush_.Reset();
        issue_brush_.Reset();
    }
    EndPaint(window_, &paint);
}

std::optional<std::size_t> LyricsView::HitTest(const POINT point) const {
    for (const auto& [rect, index] : hit_rows_) {
        if (PtInRect(&rect, point)) {
            return index;
        }
    }
    return std::nullopt;
}

}  // namespace asmr
