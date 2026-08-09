#pragma once

#include <string>
#include <string_view>

namespace asmr {

struct WorkerEventEnvelope {
    int protocol{};
    std::wstring event;
};

WorkerEventEnvelope ParseWorkerEventEnvelope(std::wstring_view json);
bool IsTerminalWorkerEvent(std::wstring_view event) noexcept;

}  // namespace asmr
