#pragma once

#include <windows.h>

namespace asmr {

bool RegisterPageHostClass(HINSTANCE instance);
HWND CreatePageHost(HWND parent, HINSTANCE instance);

}  // namespace asmr
