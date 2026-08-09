#include "page_host.hpp"

#include "app_messages.hpp"

namespace asmr {
namespace {

constexpr wchar_t kPageHostClass[] = L"ASMRTranslationPageHost";

bool ShouldForward(const UINT message) {
    return message == WM_COMMAND || message == WM_NOTIFY || message == WM_HSCROLL ||
           message == WM_VSCROLL || message == WM_APP_LYRIC_CLICK ||
           message == WM_APP_LYRIC_EDIT;
}

LRESULT CALLBACK PageHostProcedure(const HWND window,
                                   const UINT message,
                                   const WPARAM wparam,
                                   const LPARAM lparam) {
    if (ShouldForward(message)) {
        const auto root = GetAncestor(window, GA_ROOT);
        if (root != nullptr && root != window) {
            return SendMessageW(root, message, wparam, lparam);
        }
    }
    return DefWindowProcW(window, message, wparam, lparam);
}

}  // namespace

bool RegisterPageHostClass(const HINSTANCE instance) {
    WNDCLASSEXW window_class{sizeof(WNDCLASSEXW)};
    window_class.lpfnWndProc = PageHostProcedure;
    window_class.hInstance = instance;
    window_class.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    window_class.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);
    window_class.lpszClassName = kPageHostClass;
    return RegisterClassExW(&window_class) != 0 ||
           GetLastError() == ERROR_CLASS_ALREADY_EXISTS;
}

HWND CreatePageHost(const HWND parent, const HINSTANCE instance) {
    if (!RegisterPageHostClass(instance)) {
        return nullptr;
    }
    return CreateWindowExW(0,
                           kPageHostClass,
                           L"",
                           WS_CHILD | WS_VISIBLE | WS_CLIPCHILDREN | WS_CLIPSIBLINGS,
                           0,
                           0,
                           100,
                           100,
                           parent,
                           nullptr,
                           instance,
                           nullptr);
}

}  // namespace asmr
