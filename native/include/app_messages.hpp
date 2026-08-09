#pragma once

#include <windows.h>

constexpr UINT WM_APP_WORKER_EVENT = WM_APP + 1;
constexpr UINT WM_APP_WORKER_DONE = WM_APP + 2;
constexpr UINT WM_APP_MEDIA_EVENT = WM_APP + 3;
constexpr UINT WM_APP_LYRIC_CLICK = WM_APP + 4;
constexpr UINT WM_APP_LYRIC_EDIT = WM_APP + 5;

enum class WorkerChannel : WPARAM {
    Task = 1,
    Utility = 2,
};
