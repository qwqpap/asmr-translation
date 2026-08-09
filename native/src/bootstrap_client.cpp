#include "bootstrap_client.hpp"

#include "utf.hpp"
#include "worker_protocol.hpp"

#include <array>
#include <memory>
#include <stdexcept>
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

std::wstring QuoteArgument(const std::wstring& value) {
    // Paths accepted by the wizard cannot contain a quote, but escaping here also
    // keeps a user-selected mirror from changing the PowerShell command line.
    std::wstring result = L"\"";
    std::size_t slashes = 0;
    for (const auto character : value) {
        if (character == L'\\') {
            ++slashes;
            continue;
        }
        if (character == L'\"') {
            result.append(slashes * 2 + 1, L'\\');
            result.push_back(L'\"');
            slashes = 0;
            continue;
        }
        result.append(slashes, L'\\');
        slashes = 0;
        result.push_back(character);
    }
    result.append(slashes * 2, L'\\');
    result.push_back(L'\"');
    return result;
}

}  // namespace

BootstrapClient::~BootstrapClient() {
    ForceTerminate();
    Cleanup();
}

bool BootstrapClient::Start(const BootstrapOptions& options) {
    if (Running() || options.script_path.empty() || options.plan_path.empty() ||
        options.state_root.empty()) {
        return false;
    }
    Cleanup();

    SECURITY_ATTRIBUTES security{sizeof(SECURITY_ATTRIBUTES), nullptr, TRUE};
    HANDLE stdout_write = nullptr;
    if (!CreatePipe(&stdout_read_, &stdout_write, &security, 0)) {
        Cleanup();
        return false;
    }
    SetHandleInformation(stdout_read_, HANDLE_FLAG_INHERIT, 0);

    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    startup.hStdOutput = stdout_write;
    startup.hStdError = stdout_write;

    const auto powershell = L"powershell.exe";
    std::wstring command = QuoteArgument(powershell) + L" -NoLogo -NoProfile "
                           L"-NonInteractive -ExecutionPolicy Bypass -File " +
                           QuoteArgument(options.script_path.wstring()) + L" -PlanPath " +
                           QuoteArgument(options.plan_path.wstring()) + L" -StateRoot " +
                           QuoteArgument(options.state_root.wstring()) + L" -Accelerator " +
                           QuoteArgument(options.accelerator);
    if (!options.mirror_base.empty()) {
        command += L" -MirrorBase " + QuoteArgument(options.mirror_base);
    }
    if (options.install_ffmpeg) {
        command += L" -InstallFfmpeg";
    }
    if (options.install_model) {
        command += L" -InstallModel";
    }
    std::vector<wchar_t> mutable_command(command.begin(), command.end());
    mutable_command.push_back(L'\0');

    PROCESS_INFORMATION process_info{};
    const auto created = CreateProcessW(nullptr,
                                        mutable_command.data(),
                                        nullptr,
                                        nullptr,
                                        TRUE,
                                        CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP |
                                            CREATE_SUSPENDED,
                                        nullptr,
                                        options.script_path.parent_path().parent_path().c_str(),
                                        &startup,
                                        &process_info);
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
    reader_ = std::thread(&BootstrapClient::ReaderLoop, this);
    return true;
}

void BootstrapClient::Cancel() {
    // bootstrap.ps1 uses atomic temporary files, so terminating the Job is safe and
    // leaves a resumable .part file.  This is deliberately not a bare process kill.
    ForceTerminate();
}

void BootstrapClient::ForceTerminate() {
    if (!Running()) {
        return;
    }
    if (job_ != nullptr) {
        TerminateJobObject(job_, 130);
    } else if (process_ != nullptr) {
        TerminateProcess(process_, 130);
    }
}

void BootstrapClient::ReaderLoop() {
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
                (void)ParseWorkerEventEnvelope(*payload);
                PostMessageW(notify_window_, WM_APP_BOOTSTRAP_EVENT, 0,
                             reinterpret_cast<LPARAM>(payload.release()));
            } catch (...) {
                // A diagnostic line from PowerShell must never take down the GUI.
            }
        }
    }
    if (!pending.empty()) {
        try {
            auto payload = std::make_unique<std::wstring>(Utf8ToWide(pending));
            (void)ParseWorkerEventEnvelope(*payload);
            PostMessageW(notify_window_, WM_APP_BOOTSTRAP_EVENT, 0,
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
    PostMessageW(notify_window_, WM_APP_BOOTSTRAP_DONE, 0, static_cast<LPARAM>(exit_code));
}

void BootstrapClient::Cleanup() {
    if (reader_.joinable()) {
        reader_.join();
    }
    CloseHandleIfSet(stdout_read_);
    CloseHandleIfSet(process_thread_);
    CloseHandleIfSet(process_);
    CloseHandleIfSet(job_);
    running_.store(false);
}

}  // namespace asmr
