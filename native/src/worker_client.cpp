#include "worker_client.hpp"

#include "utf.hpp"
#include "worker_protocol.hpp"

#include <array>
#include <filesystem>
#include <memory>
#include <string>
#include <vector>

namespace asmr {
namespace {

void CloseHandleIfSet(HANDLE& handle) {
    if (handle != nullptr && handle != INVALID_HANDLE_VALUE) {
        CloseHandle(handle);
        handle = nullptr;
    }
}

std::wstring Quote(const std::wstring& value) {
    return L"\"" + value + L"\"";
}

}  // namespace

WorkerClient::WorkerClient(const HWND notify_window, const WorkerChannel channel)
    : notify_window_(notify_window), channel_(channel) {}

WorkerClient::~WorkerClient() {
    ForceTerminate();
    Cleanup();
}

bool WorkerClient::Start(const std::wstring& python_path,
                         const std::wstring& request_json) {
    if (Running()) {
        return false;
    }
    Cleanup();

    SECURITY_ATTRIBUTES security{sizeof(SECURITY_ATTRIBUTES), nullptr, TRUE};
    HANDLE stdin_read = nullptr;
    HANDLE stdout_write = nullptr;
    if (!CreatePipe(&stdin_read, &stdin_write_, &security, 0) ||
        !CreatePipe(&stdout_read_, &stdout_write, &security, 0)) {
        CloseHandleIfSet(stdin_read);
        CloseHandleIfSet(stdout_write);
        Cleanup();
        return false;
    }
    SetHandleInformation(stdin_write_, HANDLE_FLAG_INHERIT, 0);
    SetHandleInformation(stdout_read_, HANDLE_FLAG_INHERIT, 0);

    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput = stdin_read;
    startup.hStdOutput = stdout_write;
    startup.hStdError = stdout_write;

    PROCESS_INFORMATION process_info{};
    auto command = Quote(python_path) + L" -m asmr_lrc.gui_worker";
    std::vector<wchar_t> mutable_command(command.begin(), command.end());
    mutable_command.push_back(L'\0');
    std::wstring current_directory;
    const std::filesystem::path interpreter(python_path);
    if (std::filesystem::is_regular_file(interpreter) &&
        _wcsicmp(interpreter.parent_path().filename().c_str(), L"Scripts") == 0 &&
        _wcsicmp(interpreter.parent_path().parent_path().filename().c_str(), L".venv") == 0) {
        current_directory = interpreter.parent_path().parent_path().parent_path().wstring();
    }
    const auto created = CreateProcessW(nullptr,
                                        mutable_command.data(),
                                        nullptr,
                                        nullptr,
                                        TRUE,
                                        CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP |
                                            CREATE_SUSPENDED,
                                        nullptr,
                                        current_directory.empty() ? nullptr
                                                                  : current_directory.c_str(),
                                        &startup,
                                        &process_info);
    CloseHandleIfSet(stdin_read);
    CloseHandleIfSet(stdout_write);
    if (!created) {
        Cleanup();
        return false;
    }
    process_ = process_info.hProcess;
    process_thread_ = process_info.hThread;

    job_ = CreateJobObjectW(nullptr, nullptr);
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if (job_ == nullptr ||
        !SetInformationJobObject(
            job_, JobObjectExtendedLimitInformation, &limits, sizeof(limits)) ||
        !AssignProcessToJobObject(job_, process_) ||
        ResumeThread(process_thread_) == static_cast<DWORD>(-1)) {
        TerminateProcess(process_, 2);
        WaitForSingleObject(process_, 5000);
        Cleanup();
        return false;
    }

    running_.store(true);
    reader_ = std::thread(&WorkerClient::ReaderLoop, this);
    if (!SendControl(request_json)) {
        ForceTerminate();
        return false;
    }
    return true;
}

bool WorkerClient::SendControl(const std::wstring& request_json) {
    std::scoped_lock lock(write_mutex_);
    if (stdin_write_ == nullptr) {
        return false;
    }
    auto bytes = WideToUtf8(request_json);
    bytes.push_back('\n');
    DWORD written = 0;
    return WriteFile(stdin_write_,
                     bytes.data(),
                     static_cast<DWORD>(bytes.size()),
                     &written,
                     nullptr) &&
           written == bytes.size();
}

void WorkerClient::Cancel() {
    if (Running()) {
        SendControl(LR"({"protocol":1,"command":"cancel"})");
    }
}

void WorkerClient::ForceTerminate() {
    if (!Running()) {
        return;
    }
    if (job_ != nullptr) {
        TerminateJobObject(job_, 130);
    } else if (process_ != nullptr) {
        TerminateProcess(process_, 130);
    }
}

void WorkerClient::ReaderLoop() {
    std::array<char, 4096> buffer{};
    std::string pending;
    DWORD count = 0;
    while (ReadFile(stdout_read_, buffer.data(), static_cast<DWORD>(buffer.size()), &count, nullptr) &&
           count != 0) {
        pending.append(buffer.data(), count);
        std::size_t newline = 0;
        while ((newline = pending.find('\n')) != std::string::npos) {
            auto line = pending.substr(0, newline);
            pending.erase(0, newline + 1);
            if (!line.empty() && line.back() == '\r') {
                line.pop_back();
            }
            try {
                auto payload = std::make_unique<std::wstring>(Utf8ToWide(line));
                const auto envelope = ParseWorkerEventEnvelope(*payload);
                PostMessageW(notify_window_,
                             WM_APP_WORKER_EVENT,
                             static_cast<WPARAM>(channel_),
                             reinterpret_cast<LPARAM>(payload.release()));
                if (IsTerminalWorkerEvent(envelope.event)) {
                    std::scoped_lock lock(write_mutex_);
                    CloseHandleIfSet(stdin_write_);
                }
            } catch (...) {
            }
        }
    }
    if (!pending.empty()) {
        try {
            auto payload = std::make_unique<std::wstring>(Utf8ToWide(pending));
            PostMessageW(notify_window_,
                         WM_APP_WORKER_EVENT,
                         static_cast<WPARAM>(channel_),
                         reinterpret_cast<LPARAM>(payload.release()));
        } catch (...) {
        }
    }
    DWORD exit_code = 2;
    if (process_ != nullptr) {
        WaitForSingleObject(process_, INFINITE);
        GetExitCodeProcess(process_, &exit_code);
    }
    running_.store(false);
    PostMessageW(notify_window_,
                 WM_APP_WORKER_DONE,
                 static_cast<WPARAM>(channel_),
                 static_cast<LPARAM>(exit_code));
}

void WorkerClient::Cleanup() {
    if (reader_.joinable()) {
        reader_.join();
    }
    CloseHandleIfSet(stdin_write_);
    CloseHandleIfSet(stdout_read_);
    CloseHandleIfSet(process_thread_);
    CloseHandleIfSet(process_);
    CloseHandleIfSet(job_);
    running_.store(false);
}

}  // namespace asmr
