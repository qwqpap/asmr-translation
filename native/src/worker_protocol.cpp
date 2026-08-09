#include "worker_protocol.hpp"

#include <winrt/Windows.Data.Json.h>
#include <winrt/Windows.Foundation.Collections.h>

#include <stdexcept>

namespace asmr {

WorkerEventEnvelope ParseWorkerEventEnvelope(const std::wstring_view json) {
    const auto object = winrt::Windows::Data::Json::JsonObject::Parse(json);
    const auto protocol = static_cast<int>(object.GetNamedNumber(L"protocol", 0));
    const auto event = std::wstring(object.GetNamedString(L"event", L""));
    if (protocol != 1) {
        throw std::runtime_error("unsupported worker protocol");
    }
    if (event.empty()) {
        throw std::runtime_error("worker event name is missing");
    }
    return WorkerEventEnvelope{protocol, event};
}

bool IsTerminalWorkerEvent(const std::wstring_view event) noexcept {
    return event == L"probe_result" || event == L"result" || event == L"cues" ||
           event == L"saved" || event == L"playback_ready" ||
           event == L"download_metadata" || event == L"download_complete" ||
           event == L"error" || event == L"cancelled";
}

}  // namespace asmr
