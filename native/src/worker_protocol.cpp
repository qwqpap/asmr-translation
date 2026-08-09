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

}  // namespace asmr
