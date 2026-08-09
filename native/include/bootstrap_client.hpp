#pragma once

#include "app_messages.hpp"

#include <windows.h>

#include <atomic>
#include <filesystem>
#include <string>
#include <thread>

namespace asmr {

struct BootstrapOptions {
    std::filesystem::path script_path;
    std::filesystem::path plan_path;
    std::filesystem::path state_root;
    std::wstring mirror_base;
    std::wstring accelerator = L"cpu";
    bool install_ffmpeg = false;
    bool install_model = false;
};

class BootstrapClient {
public:
    explicit BootstrapClient(HWND notify_window) : notify_window_(notify_window) {}
    ~BootstrapClient();

    BootstrapClient(const BootstrapClient&) = delete;
    BootstrapClient& operator=(const BootstrapClient&) = delete;

    bool Start(const BootstrapOptions& options);
    void Cancel();
    void ForceTerminate();
    [[nodiscard]] bool Running() const noexcept { return running_.load(); }

private:
    void ReaderLoop();
    void Cleanup();

    HWND notify_window_{};
    HANDLE process_{};
    HANDLE process_thread_{};
    HANDLE stdout_read_{};
    HANDLE job_{};
    std::thread reader_;
    std::atomic_bool running_{false};
};

}  // namespace asmr
