#pragma once

#include "app_messages.hpp"

#include <windows.h>

#include <atomic>
#include <mutex>
#include <string>
#include <thread>

namespace asmr {

class WorkerClient {
public:
    WorkerClient(HWND notify_window, WorkerChannel channel);
    ~WorkerClient();

    WorkerClient(const WorkerClient&) = delete;
    WorkerClient& operator=(const WorkerClient&) = delete;

    bool Start(const std::wstring& python_path, const std::wstring& request_json);
    bool SendControl(const std::wstring& request_json);
    void Cancel();
    void ForceTerminate();
    [[nodiscard]] bool Running() const noexcept { return running_.load(); }

private:
    void ReaderLoop();
    void Cleanup();

    HWND notify_window_{};
    WorkerChannel channel_{};
    HANDLE process_{};
    HANDLE process_thread_{};
    HANDLE stdin_write_{};
    HANDLE stdout_read_{};
    HANDLE job_{};
    std::thread reader_;
    std::atomic_bool running_{false};
    std::mutex write_mutex_;
};

}  // namespace asmr
