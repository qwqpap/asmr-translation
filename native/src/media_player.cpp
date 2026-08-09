#include "media_player.hpp"

#include "app_messages.hpp"
#include "utf.hpp"

#include <audioclient.h>
#include <mfapi.h>
#include <mferror.h>
#include <oleauto.h>
#include <wrl/client.h>

#include <algorithm>
#include <atomic>
#include <cmath>

namespace asmr {
namespace {

class MediaEngineNotify final : public IMFMediaEngineNotify {
public:
    explicit MediaEngineNotify(const HWND window) : window_(window) {}

    HRESULT STDMETHODCALLTYPE QueryInterface(const IID& iid, void** object) override {
        if (object == nullptr) {
            return E_POINTER;
        }
        if (iid == __uuidof(IUnknown) || iid == __uuidof(IMFMediaEngineNotify)) {
            *object = static_cast<IMFMediaEngineNotify*>(this);
            AddRef();
            return S_OK;
        }
        *object = nullptr;
        return E_NOINTERFACE;
    }

    ULONG STDMETHODCALLTYPE AddRef() override { return ++references_; }

    ULONG STDMETHODCALLTYPE Release() override {
        const auto remaining = --references_;
        if (remaining == 0) {
            delete this;
        }
        return remaining;
    }

    HRESULT STDMETHODCALLTYPE EventNotify(const DWORD event_code,
                                          DWORD_PTR,
                                          DWORD) override {
        PostMessageW(window_, WM_APP_MEDIA_EVENT, event_code, 0);
        return S_OK;
    }

private:
    std::atomic_ulong references_{1};
    HWND window_{};
};

}  // namespace

MediaPlayer::~MediaPlayer() {
    Close();
    if (media_foundation_started_) {
        MFShutdown();
    }
}

bool MediaPlayer::Initialize(const HWND notify_window) {
    if (media_foundation_started_) {
        return true;
    }
    if (FAILED(MFStartup(MF_VERSION))) {
        return false;
    }
    media_foundation_started_ = true;

    Microsoft::WRL::ComPtr<IMFMediaEngineClassFactory> factory;
    if (FAILED(CoCreateInstance(CLSID_MFMediaEngineClassFactory,
                                nullptr,
                                CLSCTX_INPROC_SERVER,
                                IID_PPV_ARGS(&factory)))) {
        return false;
    }
    Microsoft::WRL::ComPtr<IMFAttributes> attributes;
    if (FAILED(MFCreateAttributes(&attributes, 2))) {
        return false;
    }
    auto* notify = new MediaEngineNotify(notify_window);
    const auto callback_result = attributes->SetUnknown(MF_MEDIA_ENGINE_CALLBACK, notify);
    notify->Release();
    if (FAILED(callback_result)) {
        return false;
    }
    attributes->SetUINT32(MF_MEDIA_ENGINE_AUDIO_CATEGORY, AudioCategory_Media);
    return SUCCEEDED(
        factory->CreateInstance(MF_MEDIA_ENGINE_AUDIOONLY, attributes.Get(), &engine_));
}

bool MediaPlayer::Open(const std::filesystem::path& path) {
    if (!engine_) {
        open_error_ = MF_E_PLATFORM_NOT_INITIALIZED;
        return false;
    }
    ready_ = false;
    source_unsupported_ = false;
    error_code_ = 0;
    extended_error_ = S_OK;
    open_error_ = S_OK;
    source_ = std::filesystem::absolute(path);
    try {
        const auto uri = FilePathToUri(source_);
        BSTR source = SysAllocString(uri.c_str());
        if (source == nullptr) {
            open_error_ = E_OUTOFMEMORY;
            return false;
        }
        const auto result = engine_->SetSource(source);
        SysFreeString(source);
        if (FAILED(result)) {
            open_error_ = result;
            return false;
        }
        open_error_ = engine_->Load();
        return SUCCEEDED(open_error_);
    } catch (...) {
        open_error_ = E_FAIL;
        return false;
    }
}

void MediaPlayer::Close() {
    if (engine_) {
        engine_->Pause();
        BSTR empty = SysAllocString(L"");
        if (empty != nullptr) {
            engine_->SetSource(empty);
            SysFreeString(empty);
        }
    }
    ready_ = false;
    source_.clear();
}

void MediaPlayer::PlayPause() {
    if (!engine_ || !ready_) {
        return;
    }
    if (engine_->IsPaused()) {
        engine_->Play();
    } else {
        engine_->Pause();
    }
}

void MediaPlayer::Seek(const double seconds) {
    if (engine_ && ready_) {
        engine_->SetCurrentTime(std::clamp(seconds, 0.0, Duration()));
    }
}

void MediaPlayer::SetVolume(const double volume) {
    if (engine_) {
        engine_->SetVolume(std::clamp(volume, 0.0, 1.0));
    }
}

void MediaPlayer::SetRate(const double rate) {
    if (engine_) {
        engine_->SetPlaybackRate(std::clamp(rate, 0.75, 2.0));
    }
}

double MediaPlayer::Position() const {
    return engine_ ? engine_->GetCurrentTime() : 0.0;
}

double MediaPlayer::Duration() const {
    if (!engine_) {
        return 0.0;
    }
    const auto duration = engine_->GetDuration();
    return std::isfinite(duration) ? duration : 0.0;
}

bool MediaPlayer::Paused() const {
    return !engine_ || engine_->IsPaused();
}

void MediaPlayer::HandleEvent(const DWORD event_code) {
    if (event_code == MF_MEDIA_ENGINE_EVENT_CANPLAY ||
        event_code == MF_MEDIA_ENGINE_EVENT_LOADEDMETADATA) {
        ready_ = true;
        source_unsupported_ = false;
    } else if (event_code == MF_MEDIA_ENGINE_EVENT_ERROR) {
        ready_ = false;
        source_unsupported_ = true;
        Microsoft::WRL::ComPtr<IMFMediaError> error;
        if (engine_ && SUCCEEDED(engine_->GetError(&error)) && error) {
            error_code_ = error->GetErrorCode();
            extended_error_ = error->GetExtendedErrorCode();
        }
    } else if (event_code == MF_MEDIA_ENGINE_EVENT_ENDED) {
        engine_->Pause();
    }
}

}  // namespace asmr
