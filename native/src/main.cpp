#include "app_messages.hpp"
#include "bootstrap_client.hpp"
#include "lrc.hpp"
#include "lyrics_view.hpp"
#include "media_player.hpp"
#include "page_host.hpp"
#include "settings.hpp"
#include "utf.hpp"
#include "worker_client.hpp"
#include "worker_protocol.hpp"

#include <windows.h>
#include <commctrl.h>
#include <mfmediaengine.h>
#include <shellapi.h>
#include <shobjidl.h>
#include <winrt/Windows.Data.Json.h>
#include <winrt/Windows.Foundation.Collections.h>
#include <winrt/base.h>
#include <windowsx.h>

#include <algorithm>
#include <cwctype>
#include <deque>
#include <filesystem>
#include <format>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace {

using winrt::Windows::Data::Json::JsonArray;
using winrt::Windows::Data::Json::JsonObject;
using winrt::Windows::Data::Json::JsonValue;
using winrt::Windows::Data::Json::JsonValueType;

constexpr wchar_t kMainClass[] = L"ASMRTranslationMainWindow";
constexpr wchar_t kDraftCredential[] = L"ASMRTranslation/OpenAI/Draft";
constexpr wchar_t kReviewCredential[] = L"ASMRTranslation/OpenAI/Review";
constexpr wchar_t kProjectUrl[] = L"https://github.com/qwqpap/asmr-translation";
constexpr UINT_PTR kPlayerTimer = 1;
constexpr UINT_PTR kCancelTimer = 2;
constexpr UINT_PTR kDownloadCancelTimer = 3;
constexpr wchar_t kSetupClass[] = L"ASMRTranslationSetupWindow";

enum ControlId : int {
    IdTab = 10,
    IdFolder = 100,
    IdBrowseFolder,
    IdProbe,
    IdRun,
    IdCancel,
    IdOverwrite,
    IdTaskProgress,
    IdTaskList,
    IdLog,
    IdOpenAudio = 200,
    IdPrevious,
    IdPlay,
    IdNext,
    IdTime,
    IdSeek,
    IdVolume,
    IdSpeed,
    IdLyrics,
    IdLyricEdit,
    IdLyricSave,
    IdPython = 300,
    IdAsrModel,
    IdFfmpeg,
    IdCache,
    IdGlossary,
    IdDraftKind,
    IdDraftProtocol,
    IdDraftBase,
    IdDraftModel,
    IdDraftKey,
    IdAnalysisModel,
    IdFallbackModel,
    IdReviewSame,
    IdReviewKind,
    IdReviewBase,
    IdReviewModel,
    IdReviewKey,
    IdReviewEnabled,
    IdQuality,
    IdSaveSettings,
    IdDownloadSettingsRoot = 340,
    IdDownloadEndpoint,
    IdCurlPath,
    IdDownloadProxy,
    IdDownloadTimeout,
    IdDownloadAbout,
    IdDownloadRj = 400,
    IdDownloadQuery,
    IdDownloadRun,
    IdDownloadCancel,
    IdDownloadRoot,
    IdDownloadBrowse,
    IdDownloadAuto,
    IdDownloadSmart,
    IdDownloadAudio,
    IdDownloadAll,
    IdDownloadNone,
    IdDownloadProgress,
    IdDownloadFiles,
    IdDownloadLog,
    IdSetupPython = 500,
    IdSetupAsr,
    IdSetupFfmpeg,
    IdSetupModel,
    IdSetupCpu,
    IdSetupCuda,
    IdSetupMirror,
    IdSetupInstall,
    IdSetupCancel,
    IdSetupSkip,
    IdSetupProgress,
    IdSetupLog,
    IdSetupOpen,
};

std::wstring TextOf(const HWND control) {
    const auto length = GetWindowTextLengthW(control);
    std::wstring result(static_cast<std::size_t>(length) + 1, L'\0');
    GetWindowTextW(control, result.data(), length + 1);
    result.resize(static_cast<std::size_t>(length));
    return result;
}

void SetText(const HWND control, const std::wstring& text) {
    SetWindowTextW(control, text.c_str());
}

HWND CreateControl(const wchar_t* class_name,
                   const wchar_t* text,
                   const DWORD style,
                   const HWND parent,
                   const int id,
                   const DWORD extended = 0) {
    return CreateWindowExW(extended,
                           class_name,
                           text,
                           WS_CHILD | WS_VISIBLE | style,
                           0,
                           0,
                           100,
                           24,
                           parent,
                           reinterpret_cast<HMENU>(static_cast<INT_PTR>(id)),
                           GetModuleHandleW(nullptr),
                           nullptr);
}

HWND CreateLabel(const HWND parent, const wchar_t* text) {
    return CreateControl(L"STATIC", text, SS_LEFT | SS_CENTERIMAGE, parent, 0);
}

std::optional<std::filesystem::path> PickFolder(const HWND owner) {
    winrt::com_ptr<IFileOpenDialog> dialog;
    if (FAILED(CoCreateInstance(CLSID_FileOpenDialog,
                                nullptr,
                                CLSCTX_INPROC_SERVER,
                                IID_PPV_ARGS(dialog.put())))) {
        return std::nullopt;
    }
    DWORD options = 0;
    dialog->GetOptions(&options);
    dialog->SetOptions(options | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM);
    if (FAILED(dialog->Show(owner))) {
        return std::nullopt;
    }
    winrt::com_ptr<IShellItem> item;
    if (FAILED(dialog->GetResult(item.put()))) {
        return std::nullopt;
    }
    PWSTR raw = nullptr;
    if (FAILED(item->GetDisplayName(SIGDN_FILESYSPATH, &raw))) {
        return std::nullopt;
    }
    std::filesystem::path result(raw);
    CoTaskMemFree(raw);
    return result;
}

std::optional<std::filesystem::path> PickAudio(const HWND owner) {
    winrt::com_ptr<IFileOpenDialog> dialog;
    if (FAILED(CoCreateInstance(CLSID_FileOpenDialog,
                                nullptr,
                                CLSCTX_INPROC_SERVER,
                                IID_PPV_ARGS(dialog.put())))) {
        return std::nullopt;
    }
    const COMDLG_FILTERSPEC filters[] = {
        {L"音频文件", L"*.mp3;*.m4a;*.flac;*.wav;*.opus;*.ogg;*.aac"},
        {L"所有文件", L"*.*"},
    };
    dialog->SetFileTypes(static_cast<UINT>(std::size(filters)), filters);
    dialog->SetOptions(FOS_FILEMUSTEXIST | FOS_FORCEFILESYSTEM);
    if (FAILED(dialog->Show(owner))) {
        return std::nullopt;
    }
    winrt::com_ptr<IShellItem> item;
    dialog->GetResult(item.put());
    PWSTR raw = nullptr;
    if (!item || FAILED(item->GetDisplayName(SIGDN_FILESYSPATH, &raw))) {
        return std::nullopt;
    }
    std::filesystem::path result(raw);
    CoTaskMemFree(raw);
    return result;
}

void PutString(JsonObject& object, const wchar_t* name, const std::wstring& value) {
    object.SetNamedValue(name, JsonValue::CreateStringValue(value));
}

void PutBoolean(JsonObject& object, const wchar_t* name, const bool value) {
    object.SetNamedValue(name, JsonValue::CreateBooleanValue(value));
}

void PutNumber(JsonObject& object, const wchar_t* name, const double value) {
    object.SetNamedValue(name, JsonValue::CreateNumberValue(value));
}

std::wstring FormatTime(const double seconds) {
    const auto total = std::max(0, static_cast<int>(seconds));
    return std::format(L"{:02}:{:02}", total / 60, total % 60);
}

bool IsAudioFile(const std::wstring& path, const std::wstring& kind) {
    if (kind == L"audio") {
        return true;
    }
    auto suffix = std::filesystem::path(path).extension().wstring();
    std::ranges::transform(suffix, suffix.begin(), towlower);
    for (const auto* value : {L".mp3", L".m4a", L".aac", L".opus", L".ogg", L".flac",
                              L".wav", L".wma"}) {
        if (suffix == value) {
            return true;
        }
    }
    return false;
}

class Application {
public:
    explicit Application(const HINSTANCE instance) : instance_(instance) {}

    bool Create();
    void OpenOnStartup(const std::filesystem::path& path);
    LRESULT HandleMessage(UINT message, WPARAM wparam, LPARAM lparam);
    [[nodiscard]] HWND Window() const noexcept { return window_; }

private:
    enum class UtilityAction { None, LoadCues, SaveEdits, PreparePlayback };

    static LRESULT CALLBACK WindowProc(HWND window, UINT message, WPARAM wparam, LPARAM lparam);
    void CreatePages();
    void CreateTaskPage();
    void CreatePlayerPage();
    void CreateSettingsPage();
    void CreateDownloadPage();
    void Layout();
    void SelectPage();
    void AppendLog(const std::wstring& line);
    void AppendDownloadLog(const std::wstring& line);
    void LoadSettingsIntoControls();
    void ReadSettingsFromControls();
    void UpdateSettingsEnabled();
    void ApplyUiFont();
    JsonObject ProviderJson(const asmr::ProviderSettings& provider,
                            const std::wstring& credential) const;
    JsonObject ConfigJson() const;
    JsonObject DownloadConfigJson() const;
    void StartProbe();
    void StartTask();
    void StartTaskRoot(const std::filesystem::path& root);
    void StartDownloadQuery();
    void StartDownloadRun();
    void QueueDownloadedTask(const std::filesystem::path& root);
    void SetDownloadSelection(int mode);
    void OpenAudio(const std::filesystem::path& path);
    void StartLoadCues();
    void StartPlaybackProxy();
    void SaveLyric();
    void HandleWorkerEvent(WorkerChannel channel, const std::wstring& json);
    void HandleWorkerDone(WorkerChannel channel, DWORD exit_code);
    void HandlePlan(const JsonObject& event);
    void HandleDownloadMetadata(const JsonObject& event);
    void HandleCues(const JsonObject& event);
    void MaybeShowSetupWizard();
    void ShowSetupWizard();
    void CloseSetupWizard(bool completed = false);
    void StartBootstrap();
    void HandleBootstrapEvent(const std::wstring& json);
    void HandleBootstrapDone(DWORD exit_code);
    LRESULT HandleSetupMessage(UINT message, WPARAM wparam, LPARAM lparam);
    static LRESULT CALLBACK SetupWindowProc(HWND window,
                                             UINT message,
                                             WPARAM wparam,
                                             LPARAM lparam);
    void LayoutSetupWindow();
    void AppendSetupLog(const std::wstring& line);
    void UpdatePlayer();
    void NavigatePlaylist(int direction);

    HINSTANCE instance_{};
    HWND window_{};
    HWND tab_{};
    HWND task_page_{};
    HWND player_page_{};
    HWND settings_page_{};
    HWND download_page_{};

    HWND folder_{};
    HWND browse_folder_{};
    HWND probe_{};
    HWND run_{};
    HWND cancel_{};
    HWND overwrite_{};
    HWND task_progress_{};
    HWND task_list_{};
    HWND log_{};

    HWND download_rj_{};
    HWND download_rj_label_{};
    HWND download_query_{};
    HWND download_run_{};
    HWND download_cancel_{};
    HWND download_root_{};
    HWND download_root_label_{};
    HWND download_browse_{};
    HWND download_auto_{};
    HWND download_smart_{};
    HWND download_audio_{};
    HWND download_all_{};
    HWND download_none_{};
    HWND download_about_{};
    HWND download_progress_{};
    HWND download_files_{};
    HWND download_log_{};

    HWND open_audio_{};
    HWND previous_{};
    HWND play_{};
    HWND next_{};
    HWND time_{};
    HWND seek_{};
    HWND volume_{};
    HWND speed_{};
    HWND lyric_edit_{};
    HWND lyric_save_{};
    asmr::LyricsView lyrics_;

    HWND python_{};
    HWND asr_model_{};
    HWND ffmpeg_{};
    HWND cache_{};
    HWND glossary_{};
    HWND draft_kind_{};
    HWND draft_protocol_{};
    HWND draft_base_{};
    HWND draft_model_{};
    HWND draft_key_{};
    HWND analysis_model_{};
    HWND fallback_model_{};
    HWND review_same_{};
    HWND review_kind_{};
    HWND review_base_{};
    HWND review_model_{};
    HWND review_key_{};
    HWND review_enabled_{};
    HWND quality_{};
    HWND settings_download_root_{};
    HWND download_endpoint_{};
    HWND curl_path_{};
    HWND download_proxy_{};
    HWND download_timeout_{};
    HWND save_settings_{};
    HWND setup_open_{};
    std::vector<HWND> setting_labels_;
    int settings_scroll_ = 0;

    HWND setup_window_{};
    HWND setup_intro_{};
    HWND setup_heading_{};
    HWND setup_mirror_label_{};
    HWND setup_python_{};
    HWND setup_asr_{};
    HWND setup_ffmpeg_{};
    HWND setup_model_{};
    HWND setup_cpu_{};
    HWND setup_cuda_{};
    HWND setup_mirror_{};
    HWND setup_install_{};
    HWND setup_cancel_{};
    HWND setup_skip_{};
    HWND setup_progress_{};
    HWND setup_log_{};

    asmr::AppSettings settings_;
    std::unique_ptr<asmr::WorkerClient> task_worker_;
    std::unique_ptr<asmr::WorkerClient> utility_worker_;
    std::unique_ptr<asmr::WorkerClient> download_worker_;
    std::unique_ptr<asmr::BootstrapClient> bootstrap_client_;
    UtilityAction utility_action_{UtilityAction::None};
    asmr::MediaPlayer player_;
    std::filesystem::path current_audio_;
    std::vector<std::filesystem::path> playlist_;
    std::optional<std::size_t> playlist_index_;
    std::optional<std::pair<std::size_t, std::wstring>> pending_edit_;
    bool playback_proxy_pending_{};
    bool seek_dragging_{};
    std::wstring download_plan_json_;
    std::vector<std::wstring> download_file_ids_;
    std::vector<bool> download_file_audio_;
    std::vector<bool> download_file_smart_;
    std::deque<std::filesystem::path> pending_download_tasks_;
    std::wstring bootstrap_python_path_;
    std::wstring bootstrap_ffmpeg_path_;
    std::wstring bootstrap_model_path_;
    UINT dpi_{96};
    HFONT ui_font_{};
};

bool Application::Create() {
    dpi_ = GetDpiForSystem();
    WNDCLASSEXW window_class{sizeof(WNDCLASSEXW)};
    window_class.lpfnWndProc = WindowProc;
    window_class.hInstance = instance_;
    window_class.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    window_class.hIcon = LoadIconW(nullptr, IDI_APPLICATION);
    window_class.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);
    window_class.lpszClassName = kMainClass;
    if (!RegisterClassExW(&window_class) && GetLastError() != ERROR_CLASS_ALREADY_EXISTS) {
        return false;
    }
    window_ = CreateWindowExW(0,
                              kMainClass,
                              L"ASMR Translation",
                              WS_OVERLAPPEDWINDOW | WS_CLIPCHILDREN,
                              CW_USEDEFAULT,
                              CW_USEDEFAULT,
                              MulDiv(1180, static_cast<int>(dpi_), 96),
                              MulDiv(780, static_cast<int>(dpi_), 96),
                              nullptr,
                              nullptr,
                              instance_,
                              this);
    if (!window_) {
        return false;
    }
    ShowWindow(window_, SW_SHOW);
    UpdateWindow(window_);
    return true;
}

void Application::OpenOnStartup(const std::filesystem::path& path) {
    OpenAudio(path);
    if (!current_audio_.empty()) {
        TabCtrl_SetCurSel(tab_, 1);
        SelectPage();
    }
}

LRESULT CALLBACK Application::WindowProc(const HWND window,
                                         const UINT message,
                                         const WPARAM wparam,
                                         const LPARAM lparam) {
    Application* self = nullptr;
    if (message == WM_NCCREATE) {
        const auto* create = reinterpret_cast<CREATESTRUCTW*>(lparam);
        self = static_cast<Application*>(create->lpCreateParams);
        self->window_ = window;
        SetWindowLongPtrW(window, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(self));
    } else {
        self = reinterpret_cast<Application*>(GetWindowLongPtrW(window, GWLP_USERDATA));
    }
    return self != nullptr ? self->HandleMessage(message, wparam, lparam)
                           : DefWindowProcW(window, message, wparam, lparam);
}

void Application::CreatePages() {
    tab_ = CreateControl(WC_TABCONTROLW, L"", WS_TABSTOP, window_, IdTab);
    TCITEMW item{};
    item.mask = TCIF_TEXT;
    for (const auto* title : {L"任务", L"播放器", L"下载", L"设置"}) {
        item.pszText = const_cast<wchar_t*>(title);
        TabCtrl_InsertItem(tab_, TabCtrl_GetItemCount(tab_), &item);
    }
    task_page_ = asmr::CreatePageHost(tab_, instance_);
    player_page_ = asmr::CreatePageHost(tab_, instance_);
    download_page_ = asmr::CreatePageHost(tab_, instance_);
    settings_page_ = asmr::CreatePageHost(tab_, instance_);
    SetWindowLongPtrW(settings_page_, GWL_STYLE,
                      GetWindowLongPtrW(settings_page_, GWL_STYLE) | WS_VSCROLL);
    CreateTaskPage();
    CreatePlayerPage();
    CreateDownloadPage();
    CreateSettingsPage();
    SelectPage();
}

void Application::CreateTaskPage() {
    folder_ = CreateControl(L"EDIT", L"", ES_AUTOHSCROLL, task_page_, IdFolder, WS_EX_CLIENTEDGE);
    browse_folder_ = CreateControl(L"BUTTON", L"选择文件夹", BS_PUSHBUTTON, task_page_, IdBrowseFolder);
    probe_ = CreateControl(L"BUTTON", L"环境探测", BS_PUSHBUTTON, task_page_, IdProbe);
    run_ = CreateControl(L"BUTTON", L"开始处理", BS_DEFPUSHBUTTON, task_page_, IdRun);
    cancel_ = CreateControl(L"BUTTON", L"取消", BS_PUSHBUTTON, task_page_, IdCancel);
    overwrite_ = CreateControl(L"BUTTON", L"覆盖已有 LRC", BS_AUTOCHECKBOX, task_page_, IdOverwrite);
    task_progress_ = CreateControl(PROGRESS_CLASSW, L"", 0, task_page_, IdTaskProgress);
    task_list_ = CreateControl(WC_LISTVIEWW,
                               L"",
                               LVS_REPORT | LVS_SHOWSELALWAYS,
                               task_page_,
                               IdTaskList,
                               WS_EX_CLIENTEDGE);
    ListView_SetExtendedListViewStyle(task_list_, LVS_EX_FULLROWSELECT | LVS_EX_DOUBLEBUFFER);
    LVCOLUMNW column{LVCF_TEXT | LVCF_WIDTH};
    column.cx = 120;
    column.pszText = const_cast<wchar_t*>(L"状态");
    ListView_InsertColumn(task_list_, 0, &column);
    column.cx = 760;
    column.pszText = const_cast<wchar_t*>(L"音频");
    ListView_InsertColumn(task_list_, 1, &column);
    log_ = CreateControl(L"EDIT",
                         L"",
                         ES_MULTILINE | ES_AUTOVSCROLL | ES_READONLY | WS_VSCROLL,
                         task_page_,
                         IdLog,
                         WS_EX_CLIENTEDGE);
    EnableWindow(cancel_, FALSE);
}

void Application::CreatePlayerPage() {
    open_audio_ = CreateControl(L"BUTTON", L"打开音频", BS_PUSHBUTTON, player_page_, IdOpenAudio);
    previous_ = CreateControl(L"BUTTON", L"上一首", BS_PUSHBUTTON, player_page_, IdPrevious);
    play_ = CreateControl(L"BUTTON", L"播放", BS_DEFPUSHBUTTON, player_page_, IdPlay);
    next_ = CreateControl(L"BUTTON", L"下一首", BS_PUSHBUTTON, player_page_, IdNext);
    time_ = CreateLabel(player_page_, L"00:00 / 00:00");
    seek_ = CreateControl(TRACKBAR_CLASSW, L"", TBS_HORZ, player_page_, IdSeek);
    SendMessageW(seek_, TBM_SETRANGE, TRUE, MAKELONG(0, 10000));
    volume_ = CreateControl(TRACKBAR_CLASSW, L"", TBS_HORZ, player_page_, IdVolume);
    SendMessageW(volume_, TBM_SETRANGE, TRUE, MAKELONG(0, 100));
    SendMessageW(volume_, TBM_SETPOS, TRUE, 80);
    speed_ = CreateControl(WC_COMBOBOXW,
                           L"",
                           CBS_DROPDOWNLIST | WS_VSCROLL,
                           player_page_,
                           IdSpeed);
    for (const auto* value : {L"0.75x", L"1.0x", L"1.25x", L"1.5x", L"2.0x"}) {
        ComboBox_AddString(speed_, value);
    }
    ComboBox_SetCurSel(speed_, 1);
    lyrics_.Create(player_page_, instance_, IdLyrics);
    lyric_edit_ = CreateControl(L"EDIT",
                                L"双击台词后可在这里修改中文",
                                ES_AUTOHSCROLL,
                                player_page_,
                                IdLyricEdit,
                                WS_EX_CLIENTEDGE);
    lyric_save_ = CreateControl(L"BUTTON", L"保存修改", BS_PUSHBUTTON, player_page_, IdLyricSave);
}

void Application::CreateDownloadPage() {
    download_rj_label_ = CreateLabel(download_page_, L"RJ / DLsite 链接");
    download_rj_ = CreateControl(L"EDIT", L"RJ01528633", ES_AUTOHSCROLL,
                                 download_page_, IdDownloadRj, WS_EX_CLIENTEDGE);
    download_query_ = CreateControl(L"BUTTON", L"查询作品", BS_DEFPUSHBUTTON,
                                    download_page_, IdDownloadQuery);
    download_run_ = CreateControl(L"BUTTON", L"开始下载", BS_PUSHBUTTON,
                                  download_page_, IdDownloadRun);
    download_cancel_ = CreateControl(L"BUTTON", L"取消下载", BS_PUSHBUTTON,
                                     download_page_, IdDownloadCancel);
    download_root_label_ = CreateLabel(download_page_, L"输出目录");
    download_root_ = CreateControl(L"EDIT", L"", ES_AUTOHSCROLL,
                                   download_page_, IdDownloadRoot, WS_EX_CLIENTEDGE);
    download_browse_ = CreateControl(L"BUTTON", L"选择目录", BS_PUSHBUTTON,
                                     download_page_, IdDownloadBrowse);
    download_auto_ = CreateControl(L"BUTTON", L"下载完成后自动处理", BS_AUTOCHECKBOX,
                                   download_page_, IdDownloadAuto);
    download_smart_ = CreateControl(L"BUTTON", L"智能音频", BS_PUSHBUTTON,
                                    download_page_, IdDownloadSmart);
    download_audio_ = CreateControl(L"BUTTON", L"全部音频", BS_PUSHBUTTON,
                                    download_page_, IdDownloadAudio);
    download_all_ = CreateControl(L"BUTTON", L"全部文件", BS_PUSHBUTTON,
                                  download_page_, IdDownloadAll);
    download_none_ = CreateControl(L"BUTTON", L"全不选", BS_PUSHBUTTON,
                                   download_page_, IdDownloadNone);
    download_about_ = CreateControl(L"BUTTON", L"许可证 / 源码", BS_PUSHBUTTON,
                                    download_page_, IdDownloadAbout);
    download_progress_ = CreateControl(PROGRESS_CLASSW, L"", 0,
                                       download_page_, IdDownloadProgress);
    download_files_ = CreateControl(WC_LISTVIEWW, L"",
                                    LVS_REPORT | LVS_SHOWSELALWAYS | LVS_SINGLESEL,
                                    download_page_, IdDownloadFiles, WS_EX_CLIENTEDGE);
    ListView_SetExtendedListViewStyle(download_files_,
                                      LVS_EX_CHECKBOXES | LVS_EX_FULLROWSELECT |
                                          LVS_EX_DOUBLEBUFFER);
    LVCOLUMNW column{LVCF_TEXT | LVCF_WIDTH};
    column.cx = 90;
    column.pszText = const_cast<wchar_t*>(L"类型");
    ListView_InsertColumn(download_files_, 0, &column);
    column.cx = 620;
    column.pszText = const_cast<wchar_t*>(L"文件");
    ListView_InsertColumn(download_files_, 1, &column);
    column.cx = 120;
    column.pszText = const_cast<wchar_t*>(L"大小");
    ListView_InsertColumn(download_files_, 2, &column);
    download_log_ = CreateControl(L"EDIT", L"",
                                  ES_MULTILINE | ES_AUTOVSCROLL | ES_READONLY | WS_VSCROLL,
                                  download_page_, IdDownloadLog, WS_EX_CLIENTEDGE);
    EnableWindow(download_run_, FALSE);
    EnableWindow(download_cancel_, FALSE);
    Button_SetCheck(download_auto_, BST_CHECKED);
    SetText(download_root_, settings_.download_root);
}

void Application::CreateSettingsPage() {
    auto add_row = [this](const wchar_t* label, HWND& control, const int id, const DWORD style) {
        setting_labels_.push_back(CreateLabel(settings_page_, label));
        control = CreateControl(style == CBS_DROPDOWNLIST ? WC_COMBOBOXW : L"EDIT",
                                L"",
                                style == CBS_DROPDOWNLIST ? CBS_DROPDOWNLIST | WS_VSCROLL
                                                         : style,
                                settings_page_,
                                id,
                                style == CBS_DROPDOWNLIST ? 0 : WS_EX_CLIENTEDGE);
    };
    add_row(L"Python 解释器", python_, IdPython, ES_AUTOHSCROLL);
    add_row(L"ASR 模型目录", asr_model_, IdAsrModel, ES_AUTOHSCROLL);
    add_row(L"FFmpeg", ffmpeg_, IdFfmpeg, ES_AUTOHSCROLL);
    add_row(L"缓存目录", cache_, IdCache, ES_AUTOHSCROLL);
    add_row(L"固定术语 JSON", glossary_, IdGlossary, ES_AUTOHSCROLL);
    add_row(L"下载资料库", settings_download_root_, IdDownloadSettingsRoot, ES_AUTOHSCROLL);
    add_row(L"下载 endpoint", download_endpoint_, IdDownloadEndpoint, ES_AUTOHSCROLL);
    add_row(L"curl.exe", curl_path_, IdCurlPath, ES_AUTOHSCROLL);
    add_row(L"下载代理", download_proxy_, IdDownloadProxy, ES_AUTOHSCROLL);
    add_row(L"连接超时(秒)", download_timeout_, IdDownloadTimeout, ES_AUTOHSCROLL);
    add_row(L"初译提供方", draft_kind_, IdDraftKind, CBS_DROPDOWNLIST);
    add_row(L"翻译协议", draft_protocol_, IdDraftProtocol, CBS_DROPDOWNLIST);
    add_row(L"初译 Base URL", draft_base_, IdDraftBase, ES_AUTOHSCROLL);
    add_row(L"初译模型", draft_model_, IdDraftModel, ES_AUTOHSCROLL);
    add_row(L"初译 API Key", draft_key_, IdDraftKey, ES_PASSWORD | ES_AUTOHSCROLL);
    add_row(L"语境分析模型（可选）", analysis_model_, IdAnalysisModel, ES_AUTOHSCROLL);
    add_row(L"失败兜底模型（可选）", fallback_model_, IdFallbackModel, ES_AUTOHSCROLL);
    review_same_ = CreateControl(L"BUTTON",
                                 L"审校使用与初译相同的提供方",
                                 BS_AUTOCHECKBOX,
                                 settings_page_,
                                 IdReviewSame);
    add_row(L"审校提供方", review_kind_, IdReviewKind, CBS_DROPDOWNLIST);
    add_row(L"审校 Base URL", review_base_, IdReviewBase, ES_AUTOHSCROLL);
    add_row(L"审校模型", review_model_, IdReviewModel, ES_AUTOHSCROLL);
    add_row(L"审校 API Key", review_key_, IdReviewKey, ES_PASSWORD | ES_AUTOHSCROLL);
    for (const auto combo : {draft_kind_, review_kind_}) {
        ComboBox_AddString(combo, L"Ollama");
        ComboBox_AddString(combo, L"OpenAI 兼容");
    }
    ComboBox_AddString(draft_protocol_, L"chat-json");
    ComboBox_AddString(draft_protocol_, L"translategemma");
    review_enabled_ = CreateControl(L"BUTTON",
                                    L"启用全量二次审校",
                                    BS_AUTOCHECKBOX,
                                    settings_page_,
                                    IdReviewEnabled);
    quality_ = CreateControl(L"BUTTON",
                             L"质量模式：语境分析",
                             BS_AUTOCHECKBOX,
                             settings_page_,
                             IdQuality);
    save_settings_ = CreateControl(L"BUTTON", L"保存设置", BS_DEFPUSHBUTTON, settings_page_, IdSaveSettings);
    setup_open_ = CreateControl(L"BUTTON", L"依赖向导", BS_PUSHBUTTON, settings_page_, IdSetupOpen);
}

void Application::Layout() {
    RECT client{};
    GetClientRect(window_, &client);
    const auto scale = [this](const int value) {
        return MulDiv(value, static_cast<int>(dpi_), 96);
    };
    const int margin = scale(10);
    MoveWindow(tab_, margin, margin, client.right - 2 * margin, client.bottom - 2 * margin, TRUE);
    RECT page{};
    GetClientRect(tab_, &page);
    TabCtrl_AdjustRect(tab_, FALSE, &page);
    for (const auto child : {task_page_, player_page_, download_page_, settings_page_}) {
        MoveWindow(child, page.left, page.top, page.right - page.left, page.bottom - page.top, TRUE);
    }
    const int width = page.right - page.left;
    const int height = page.bottom - page.top;

    MoveWindow(folder_,
               scale(10),
               scale(10),
               std::max(scale(100), width - scale(550)),
               scale(28),
               TRUE);
    MoveWindow(browse_folder_, width - scale(530), scale(10), scale(105), scale(28), TRUE);
    MoveWindow(probe_, width - scale(415), scale(10), scale(90), scale(28), TRUE);
    MoveWindow(run_, width - scale(315), scale(10), scale(90), scale(28), TRUE);
    MoveWindow(cancel_, width - scale(215), scale(10), scale(70), scale(28), TRUE);
    MoveWindow(overwrite_, width - scale(135), scale(10), scale(130), scale(28), TRUE);
    MoveWindow(task_progress_, scale(10), scale(48), width - scale(20), scale(18), TRUE);
    const int list_height = std::max(scale(150), (height - scale(86)) * 55 / 100);
    MoveWindow(task_list_, scale(10), scale(76), width - scale(20), list_height, TRUE);
    MoveWindow(log_,
               scale(10),
               scale(86) + list_height,
               width - scale(20),
               height - list_height - scale(96),
               TRUE);

    MoveWindow(open_audio_, scale(10), scale(10), scale(92), scale(28), TRUE);
    MoveWindow(previous_, scale(112), scale(10), scale(72), scale(28), TRUE);
    MoveWindow(play_, scale(194), scale(10), scale(72), scale(28), TRUE);
    MoveWindow(next_, scale(276), scale(10), scale(72), scale(28), TRUE);
    MoveWindow(time_, scale(360), scale(10), scale(150), scale(28), TRUE);
    MoveWindow(speed_, width - scale(100), scale(10), scale(90), scale(160), TRUE);
    MoveWindow(volume_, width - scale(260), scale(10), scale(150), scale(28), TRUE);
    MoveWindow(seek_, scale(10), scale(46), width - scale(20), scale(34), TRUE);
    MoveWindow(lyrics_.Window(),
               scale(10),
               scale(84),
               width - scale(20),
               std::max(scale(180), height - scale(140)),
               TRUE);
    MoveWindow(lyric_edit_,
               scale(10),
               height - scale(46),
               width - scale(125),
               scale(28),
               TRUE);
    MoveWindow(lyric_save_,
               width - scale(105),
               height - scale(46),
               scale(95),
               scale(28),
               TRUE);

    MoveWindow(download_rj_label_, scale(10), scale(10), scale(125), scale(28), TRUE);
    MoveWindow(download_rj_, scale(140), scale(10), width - scale(460), scale(28), TRUE);
    MoveWindow(download_query_, width - scale(310), scale(10), scale(95), scale(28), TRUE);
    MoveWindow(download_run_, width - scale(205), scale(10), scale(95), scale(28), TRUE);
    MoveWindow(download_cancel_, width - scale(100), scale(10), scale(90), scale(28), TRUE);
    MoveWindow(download_root_label_, scale(10), scale(46), scale(125), scale(28), TRUE);
    MoveWindow(download_root_, scale(140), scale(46), width - scale(320), scale(28), TRUE);
    MoveWindow(download_browse_, width - scale(165), scale(46), scale(145), scale(28), TRUE);
    MoveWindow(download_auto_, scale(140), scale(82), scale(180), scale(28), TRUE);
    MoveWindow(download_smart_, scale(330), scale(82), scale(85), scale(28), TRUE);
    MoveWindow(download_audio_, scale(425), scale(82), scale(85), scale(28), TRUE);
    MoveWindow(download_all_, scale(520), scale(82), scale(85), scale(28), TRUE);
    MoveWindow(download_none_, scale(615), scale(82), scale(85), scale(28), TRUE);
    MoveWindow(download_about_, width - scale(145), scale(82), scale(125), scale(28), TRUE);
    MoveWindow(download_progress_, scale(10), scale(118), width - scale(20), scale(18), TRUE);
    const int download_list_height = std::max(scale(150), (height - scale(154)) * 55 / 100);
    MoveWindow(download_files_, scale(10), scale(142), width - scale(20), download_list_height, TRUE);
    MoveWindow(download_log_, scale(10), scale(148) + download_list_height,
               width - scale(20), height - download_list_height - scale(158), TRUE);

    int y = scale(18);
    const int previous_settings_scroll = settings_scroll_;
    const int content_top = y;
    const int content_offset = settings_scroll_;
    std::size_t label_index = 0;
    for (const auto control : {python_,
                               asr_model_,
                               ffmpeg_,
                               cache_,
                               glossary_,
                               settings_download_root_,
                               download_endpoint_,
                               curl_path_,
                               download_proxy_,
                               download_timeout_,
                               draft_kind_,
                               draft_protocol_,
                                draft_base_,
                                draft_model_,
                                draft_key_,
                                analysis_model_,
                                fallback_model_}) {
        MoveWindow(setting_labels_[label_index++],
                   scale(20),
                   y - content_offset,
                   scale(140),
                   scale(28),
                   TRUE);
        MoveWindow(control,
                   scale(170),
                   y - content_offset,
                   width - scale(200),
                   scale(28),
                   TRUE);
        y += scale(32);
    }
    MoveWindow(review_same_, scale(170), y - content_offset, scale(300), scale(28), TRUE);
    y += scale(32);
    for (const auto control : {review_kind_, review_base_, review_model_, review_key_}) {
        MoveWindow(setting_labels_[label_index++],
                   scale(20),
                   y - content_offset,
                   scale(140),
                   scale(28),
                   TRUE);
        MoveWindow(control,
                   scale(170),
                   y - content_offset,
                   width - scale(200),
                   scale(28),
                   TRUE);
        y += scale(32);
    }
    MoveWindow(review_enabled_, scale(170), y - content_offset, scale(260), scale(28), TRUE);
    y += scale(32);
    MoveWindow(quality_, scale(170), y - content_offset, scale(320), scale(28), TRUE);
    MoveWindow(save_settings_, width - scale(130), y - content_offset, scale(110), scale(30), TRUE);
    MoveWindow(setup_open_, width - scale(250), y - content_offset, scale(110), scale(30), TRUE);
    const int content_height = y + scale(42) - content_top;
    const int max_scroll = std::max(0, content_height - height);
    settings_scroll_ = std::clamp(settings_scroll_, 0, max_scroll);
    SCROLLINFO scroll_info{sizeof(SCROLLINFO), SIF_RANGE | SIF_PAGE | SIF_POS};
    scroll_info.nMin = 0;
    scroll_info.nMax = content_height;
    scroll_info.nPage = static_cast<UINT>(height);
    scroll_info.nPos = settings_scroll_;
    SetScrollInfo(settings_page_, SB_VERT, &scroll_info, TRUE);
    if (settings_scroll_ != previous_settings_scroll) {
        Layout();
        return;
    }
}

void Application::SelectPage() {
    const auto selected = TabCtrl_GetCurSel(tab_);
    ShowWindow(task_page_, selected == 0 ? SW_SHOW : SW_HIDE);
    ShowWindow(player_page_, selected == 1 ? SW_SHOW : SW_HIDE);
    ShowWindow(download_page_, selected == 2 ? SW_SHOW : SW_HIDE);
    ShowWindow(settings_page_, selected == 3 ? SW_SHOW : SW_HIDE);
}

void Application::AppendLog(const std::wstring& line) {
    const auto length = GetWindowTextLengthW(log_);
    SendMessageW(log_, EM_SETSEL, length, length);
    const auto with_newline = line + L"\r\n";
    SendMessageW(log_, EM_REPLACESEL, FALSE, reinterpret_cast<LPARAM>(with_newline.c_str()));
    SendMessageW(log_, EM_SCROLLCARET, 0, 0);
}

void Application::AppendDownloadLog(const std::wstring& line) {
    const auto length = GetWindowTextLengthW(download_log_);
    SendMessageW(download_log_, EM_SETSEL, length, length);
    const auto with_newline = line + L"\r\n";
    SendMessageW(download_log_, EM_REPLACESEL, FALSE,
                 reinterpret_cast<LPARAM>(with_newline.c_str()));
    SendMessageW(download_log_, EM_SCROLLCARET, 0, 0);
}

void Application::LoadSettingsIntoControls() {
    SetText(python_, settings_.python_path);
    SetText(asr_model_, settings_.asr_model);
    SetText(ffmpeg_, settings_.ffmpeg_path);
    SetText(cache_, settings_.cache_root);
    SetText(glossary_, settings_.glossary_path);
    SetText(settings_download_root_, settings_.download_root);
    SetText(download_endpoint_, settings_.download_endpoint);
    SetText(curl_path_, settings_.curl_path);
    SetText(download_proxy_, settings_.download_proxy);
    SetText(download_timeout_, std::to_wstring(settings_.download_connect_timeout));
    SetText(download_root_, settings_.download_root);
    ComboBox_SetCurSel(draft_kind_, settings_.draft.kind == L"openai" ? 1 : 0);
    ComboBox_SetCurSel(draft_protocol_, settings_.draft.protocol == L"translategemma" ? 1 : 0);
    SetText(draft_base_, settings_.draft.base_url);
    SetText(draft_model_, settings_.draft.model);
    SetText(draft_key_, asmr::ReadCredential(kDraftCredential));
    SetText(analysis_model_, settings_.analysis_enabled ? settings_.analysis.model : L"");
    SetText(fallback_model_, settings_.fallback_enabled ? settings_.fallback.model : L"");
    Button_SetCheck(review_same_, settings_.review_same_as_draft ? BST_CHECKED : BST_UNCHECKED);
    ComboBox_SetCurSel(review_kind_, settings_.review.kind == L"openai" ? 1 : 0);
    SetText(review_base_, settings_.review.base_url);
    SetText(review_model_, settings_.review.model);
    SetText(review_key_, asmr::ReadCredential(kReviewCredential));
    Button_SetCheck(review_enabled_, settings_.review_enabled ? BST_CHECKED : BST_UNCHECKED);
    Button_SetCheck(quality_, settings_.quality_mode ? BST_CHECKED : BST_UNCHECKED);
    UpdateSettingsEnabled();
}

void Application::ReadSettingsFromControls() {
    settings_.python_path = TextOf(python_);
    settings_.asr_model = TextOf(asr_model_);
    settings_.ffmpeg_path = TextOf(ffmpeg_);
    settings_.cache_root = TextOf(cache_);
    settings_.glossary_path = TextOf(glossary_);
    const auto settings_root = TextOf(settings_download_root_);
    settings_.download_root = TabCtrl_GetCurSel(tab_) == 2 ? TextOf(download_root_) : settings_root;
    if (settings_.download_root.empty()) {
        settings_.download_root = settings_root;
    }
    settings_.download_endpoint = TextOf(download_endpoint_);
    settings_.curl_path = TextOf(curl_path_);
    settings_.download_proxy = TextOf(download_proxy_);
    try {
        settings_.download_connect_timeout = std::max(1, std::stoi(TextOf(download_timeout_)));
    } catch (...) {
        settings_.download_connect_timeout = 10;
    }
    SetText(download_root_, settings_.download_root);
    settings_.download_root = TextOf(download_root_);
    settings_.draft.kind = ComboBox_GetCurSel(draft_kind_) == 1 ? L"openai" : L"ollama";
    settings_.draft.base_url = TextOf(draft_base_);
    settings_.draft.model = TextOf(draft_model_);
    settings_.draft.protocol = ComboBox_GetCurSel(draft_protocol_) == 1
                                   ? L"translategemma"
                                   : L"chat-json";
    settings_.review_same_as_draft = Button_GetCheck(review_same_) == BST_CHECKED;
    settings_.analysis.model = TextOf(analysis_model_);
    settings_.analysis.protocol = L"chat-json";
    settings_.analysis_enabled = !settings_.analysis.model.empty();
    settings_.fallback.model = TextOf(fallback_model_);
    settings_.fallback.protocol = L"chat-json";
    settings_.fallback_enabled = !settings_.fallback.model.empty();
    settings_.review.kind = ComboBox_GetCurSel(review_kind_) == 1 ? L"openai" : L"ollama";
    settings_.review.base_url = TextOf(review_base_);
    settings_.review.model = TextOf(review_model_);
    settings_.review_enabled = Button_GetCheck(review_enabled_) == BST_CHECKED;
    settings_.quality_mode = Button_GetCheck(quality_) == BST_CHECKED;
}

void Application::UpdateSettingsEnabled() {
    const bool draft_openai = ComboBox_GetCurSel(draft_kind_) == 1;
    const bool same_review = Button_GetCheck(review_same_) == BST_CHECKED;
    const bool review_openai = ComboBox_GetCurSel(review_kind_) == 1;
    EnableWindow(draft_key_, draft_openai);
    EnableWindow(draft_protocol_, !draft_openai);
    if (draft_openai) {
        ComboBox_SetCurSel(draft_protocol_, 0);
    }
    for (const auto control : {review_kind_, review_base_, review_model_}) {
        EnableWindow(control, !same_review);
    }
    EnableWindow(review_key_, !same_review && review_openai);
}

void Application::ApplyUiFont() {
    if (ui_font_ != nullptr) {
        DeleteObject(ui_font_);
    }
    ui_font_ = CreateFontW(-MulDiv(9, static_cast<int>(dpi_), 72),
                           0,
                           0,
                           0,
                           FW_NORMAL,
                           FALSE,
                           FALSE,
                           FALSE,
                           DEFAULT_CHARSET,
                           OUT_DEFAULT_PRECIS,
                           CLIP_DEFAULT_PRECIS,
                           CLEARTYPE_QUALITY,
                           DEFAULT_PITCH | FF_DONTCARE,
                           L"Microsoft YaHei UI");
    if (ui_font_ != nullptr) {
        EnumChildWindows(
            window_,
            [](const HWND child, const LPARAM font) -> BOOL {
                SendMessageW(child, WM_SETFONT, static_cast<WPARAM>(font), TRUE);
                return TRUE;
            },
            reinterpret_cast<LPARAM>(ui_font_));
        if (setup_window_ != nullptr) {
            EnumChildWindows(
                setup_window_,
                [](const HWND child, const LPARAM font) -> BOOL {
                    SendMessageW(child, WM_SETFONT, static_cast<WPARAM>(font), TRUE);
                    return TRUE;
                },
                reinterpret_cast<LPARAM>(ui_font_));
        }
    }
}

void Application::AppendSetupLog(const std::wstring& line) {
    if (setup_log_ == nullptr) {
        return;
    }
    const auto length = GetWindowTextLengthW(setup_log_);
    SendMessageW(setup_log_, EM_SETSEL, length, length);
    const auto with_newline = line + L"\r\n";
    SendMessageW(setup_log_, EM_REPLACESEL, FALSE,
                 reinterpret_cast<LPARAM>(with_newline.c_str()));
    SendMessageW(setup_log_, EM_SCROLLCARET, 0, 0);
}

void Application::LayoutSetupWindow() {
    if (setup_window_ == nullptr) {
        return;
    }
    RECT client{};
    GetClientRect(setup_window_, &client);
    const auto scale = [this](const int value) {
        return MulDiv(value, static_cast<int>(dpi_), 96);
    };
    const int width = client.right;
    MoveWindow(setup_intro_, scale(24), scale(16), width - scale(48), scale(36), TRUE);
    MoveWindow(setup_heading_, scale(24), scale(44), width - scale(48), scale(22), TRUE);
    MoveWindow(setup_python_, scale(24), scale(60), width - scale(48), scale(28), TRUE);
    MoveWindow(setup_asr_, scale(24), scale(92), width - scale(48), scale(28), TRUE);
    MoveWindow(setup_ffmpeg_, scale(24), scale(124), width - scale(48), scale(28), TRUE);
    MoveWindow(setup_model_, scale(24), scale(156), width - scale(48), scale(28), TRUE);
    MoveWindow(setup_cpu_, scale(24), scale(196), scale(110), scale(28), TRUE);
    MoveWindow(setup_cuda_, scale(140), scale(196), scale(150), scale(28), TRUE);
    MoveWindow(setup_mirror_label_, scale(24), scale(220), width - scale(48), scale(18), TRUE);
    MoveWindow(setup_mirror_, scale(24), scale(238), width - scale(48), scale(28), TRUE);
    MoveWindow(setup_progress_, scale(24), scale(278), width - scale(48), scale(18), TRUE);
    MoveWindow(setup_log_, scale(24), scale(304), width - scale(48), scale(92), TRUE);
    MoveWindow(setup_install_, width - scale(320), scale(414), scale(92), scale(30), TRUE);
    MoveWindow(setup_cancel_, width - scale(220), scale(414), scale(92), scale(30), TRUE);
    MoveWindow(setup_skip_, width - scale(120), scale(414), scale(92), scale(30), TRUE);
}

void Application::ShowSetupWizard() {
    if (setup_window_ != nullptr) {
        SetForegroundWindow(setup_window_);
        return;
    }
    WNDCLASSEXW window_class{sizeof(WNDCLASSEXW)};
    window_class.lpfnWndProc = SetupWindowProc;
    window_class.hInstance = instance_;
    window_class.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    window_class.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);
    window_class.lpszClassName = kSetupClass;
    if (!GetClassInfoExW(instance_, kSetupClass, &window_class)) {
        if (!RegisterClassExW(&window_class) && GetLastError() != ERROR_CLASS_ALREADY_EXISTS) {
            MessageBoxW(window_, L"无法创建依赖向导窗口。", L"ASMR Translation", MB_ICONERROR);
            return;
        }
    }
    setup_window_ = CreateWindowExW(WS_EX_DLGMODALFRAME,
                                    kSetupClass,
                                    L"ASMR Translation 依赖向导",
                                    WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX,
                                    CW_USEDEFAULT,
                                    CW_USEDEFAULT,
                                    MulDiv(680, static_cast<int>(dpi_), 96),
                                    MulDiv(500, static_cast<int>(dpi_), 96),
                                    window_,
                                    nullptr,
                                    instance_,
                                    this);
    if (setup_window_ == nullptr) {
        MessageBoxW(window_, L"无法创建依赖向导窗口。", L"ASMR Translation", MB_ICONERROR);
        return;
    }
    EnableWindow(window_, FALSE);
    RECT owner{};
    RECT dialog{};
    GetWindowRect(window_, &owner);
    GetWindowRect(setup_window_, &dialog);
    const int x = owner.left + ((owner.right - owner.left) - (dialog.right - dialog.left)) / 2;
    const int y = owner.top + ((owner.bottom - owner.top) - (dialog.bottom - dialog.top)) / 2;
    SetWindowPos(setup_window_, HWND_TOP, x, y, 0, 0, SWP_NOSIZE);
    ShowWindow(setup_window_, SW_SHOW);
    UpdateWindow(setup_window_);
    ApplyUiFont();
}

void Application::CloseSetupWizard(const bool completed) {
    if (setup_window_ == nullptr) {
        return;
    }
    if (bootstrap_client_ != nullptr && bootstrap_client_->Running()) {
        bootstrap_client_->Cancel();
        return;
    }
    if (!completed) {
        settings_.setup_prompted = true;
        try {
            asmr::SaveSettings(settings_);
        } catch (...) {
        }
    }
    EnableWindow(window_, TRUE);
    DestroyWindow(setup_window_);
    setup_window_ = nullptr;
    SetForegroundWindow(window_);
}

void Application::MaybeShowSetupWizard() {
    if (setup_window_ != nullptr || settings_.setup_prompted) {
        return;
    }
    const auto python = std::filesystem::path(settings_.python_path);
    if (std::filesystem::is_regular_file(python) && !asmr::IsEmbeddedPython(settings_.python_path)) {
        settings_.setup_prompted = true;
        try {
            asmr::SaveSettings(settings_);
        } catch (...) {
        }
        return;
    }
    if (!std::filesystem::is_regular_file(asmr::BootstrapScriptPath()) ||
        !std::filesystem::is_regular_file(asmr::BootstrapManifestPath())) {
        return;
    }
    ShowSetupWizard();
}

void Application::StartBootstrap() {
    if (bootstrap_client_ == nullptr || bootstrap_client_->Running()) {
        return;
    }
    if (Button_GetCheck(setup_python_) != BST_CHECKED ||
        Button_GetCheck(setup_asr_) != BST_CHECKED) {
        MessageBoxW(setup_window_, L"必须勾选 Python 运行时和 ASR 依赖。",
                    L"ASMR Translation", MB_ICONWARNING);
        return;
    }
    const auto script = asmr::BootstrapScriptPath();
    const auto manifest = asmr::BootstrapManifestPath();
    if (!std::filesystem::is_regular_file(script) || !std::filesystem::is_regular_file(manifest)) {
        AppendSetupLog(L"找不到安装器引导资源；开发环境请继续使用 .venv，发布版请重新安装。 ");
        return;
    }
    asmr::BootstrapOptions options;
    options.script_path = script;
    options.plan_path = manifest;
    options.state_root = asmr::ApplicationDataDirectory();
    options.mirror_base = TextOf(setup_mirror_);
    options.accelerator = Button_GetCheck(setup_cuda_) == BST_CHECKED ? L"cuda" : L"cpu";
    options.install_ffmpeg = Button_GetCheck(setup_ffmpeg_) == BST_CHECKED;
    options.install_model = Button_GetCheck(setup_model_) == BST_CHECKED;
    SendMessageW(setup_progress_, PBM_SETPOS, 0, 0);
    SetText(setup_log_, L"");
    AppendSetupLog(L"开始安装所选依赖；不会上传音频或 API Key。 ");
    bootstrap_python_path_.clear();
    bootstrap_ffmpeg_path_.clear();
    bootstrap_model_path_.clear();
    if (!bootstrap_client_->Start(options)) {
        AppendSetupLog(L"无法启动 PowerShell 引导进程。 ");
        return;
    }
    EnableWindow(setup_install_, FALSE);
    EnableWindow(setup_skip_, FALSE);
    EnableWindow(setup_cancel_, TRUE);
    EnableWindow(setup_python_, FALSE);
    EnableWindow(setup_asr_, FALSE);
    EnableWindow(setup_ffmpeg_, FALSE);
    EnableWindow(setup_model_, FALSE);
    EnableWindow(setup_cpu_, FALSE);
    EnableWindow(setup_cuda_, FALSE);
    EnableWindow(setup_mirror_, FALSE);
}

void Application::HandleBootstrapEvent(const std::wstring& json) {
    JsonObject event;
    try {
        event = JsonObject::Parse(json);
    } catch (...) {
        return;
    }
    const auto name = std::wstring(event.GetNamedString(L"event", L""));
    if (name == L"download_progress") {
        const auto received = event.GetNamedNumber(L"received", 0);
        const auto total = event.GetNamedNumber(L"total", 0);
        const auto progress = total > 0 ? std::clamp(received / total, 0.0, 1.0) * 10000.0 : 0.0;
        SendMessageW(setup_progress_, PBM_SETPOS, static_cast<WPARAM>(progress), 0);
        AppendSetupLog(std::format(L"下载 {}：{:.1f}%",
                                   std::wstring(event.GetNamedString(L"name", L"文件")),
                                   progress / 100.0));
    } else if (name == L"download_start") {
        AppendSetupLog(L"开始下载 " + std::wstring(event.GetNamedString(L"name", L"文件")) + L"……");
    } else if (name == L"verify") {
        AppendSetupLog(L"校验完成：" + std::wstring(event.GetNamedString(L"name", L"文件")));
    } else if (name == L"install") {
        AppendSetupLog(L"正在安装 " + std::wstring(event.GetNamedString(L"name", L"依赖")) + L"……");
    } else if (name == L"complete") {
        bootstrap_python_path_ = std::wstring(event.GetNamedString(L"python", L""));
        bootstrap_ffmpeg_path_ = std::wstring(event.GetNamedString(L"ffmpeg", L""));
        bootstrap_model_path_ = std::wstring(event.GetNamedString(L"model", L""));
        AppendSetupLog(L"依赖安装完成，正在写入设置。 ");
    } else if (name == L"error") {
        AppendSetupLog(L"引导失败：" + std::wstring(event.GetNamedString(L"message", L"未知错误")));
    }
}

void Application::HandleBootstrapDone(const DWORD exit_code) {
    if (setup_window_ == nullptr) {
        return;
    }
    EnableWindow(setup_install_, TRUE);
    EnableWindow(setup_skip_, TRUE);
    EnableWindow(setup_cancel_, FALSE);
    EnableWindow(setup_python_, TRUE);
    EnableWindow(setup_asr_, TRUE);
    EnableWindow(setup_ffmpeg_, TRUE);
    EnableWindow(setup_model_, TRUE);
    EnableWindow(setup_cpu_, TRUE);
    EnableWindow(setup_cuda_, TRUE);
    EnableWindow(setup_mirror_, TRUE);
    if (exit_code == 0) {
        if (bootstrap_python_path_.empty()) {
            bootstrap_python_path_ = (asmr::EmbeddedRuntimeRoot() / L"python.exe").wstring();
        }
        settings_.python_path = bootstrap_python_path_;
        if (!bootstrap_ffmpeg_path_.empty()) {
            settings_.ffmpeg_path = bootstrap_ffmpeg_path_;
        }
        if (!bootstrap_model_path_.empty()) {
            settings_.asr_model = bootstrap_model_path_;
        }
        settings_.setup_prompted = true;
        settings_.setup_completed = true;
        try {
            asmr::SaveSettings(settings_);
            LoadSettingsIntoControls();
            AppendLog(L"依赖向导完成，已配置嵌入式 Python。 ");
            CloseSetupWizard(true);
        } catch (const std::exception& error) {
            AppendSetupLog(asmr::Utf8ToWide(error.what()));
        }
    } else {
        AppendSetupLog(std::format(L"引导进程退出码：{}。可修正镜像或取消选项后重试。 ", exit_code));
    }
}

LRESULT CALLBACK Application::SetupWindowProc(const HWND window,
                                               const UINT message,
                                               const WPARAM wparam,
                                               const LPARAM lparam) {
    Application* self = nullptr;
    if (message == WM_NCCREATE) {
        const auto* create = reinterpret_cast<CREATESTRUCTW*>(lparam);
        self = static_cast<Application*>(create->lpCreateParams);
        self->setup_window_ = window;
        SetWindowLongPtrW(window, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(self));
    } else {
        self = reinterpret_cast<Application*>(GetWindowLongPtrW(window, GWLP_USERDATA));
    }
    return self != nullptr ? self->HandleSetupMessage(message, wparam, lparam)
                           : DefWindowProcW(window, message, wparam, lparam);
}

LRESULT Application::HandleSetupMessage(const UINT message,
                                        const WPARAM wparam,
                                        const LPARAM lparam) {
    switch (message) {
        case WM_CREATE:
            setup_intro_ = CreateControl(
                L"STATIC",
                L"首次运行需要准备本地 Python/ASR 依赖。勾选项目并点击“安装所选”后才会联网。",
                SS_LEFT | SS_WORDELLIPSIS,
                setup_window_,
                0);
            setup_heading_ = CreateControl(L"STATIC", L"依赖选择", SS_LEFT | SS_CENTERIMAGE,
                                           setup_window_, 0);
            setup_python_ = CreateControl(L"BUTTON", L"Python 3.12 Embeddable 运行时",
                                          BS_AUTOCHECKBOX, setup_window_, IdSetupPython);
            setup_asr_ = CreateControl(L"BUTTON", L"ASR Python 依赖（faster-whisper）",
                                       BS_AUTOCHECKBOX, setup_window_, IdSetupAsr);
            setup_ffmpeg_ = CreateControl(L"BUTTON", L"可选：下载 FFmpeg",
                                          BS_AUTOCHECKBOX, setup_window_, IdSetupFfmpeg);
            setup_model_ = CreateControl(L"BUTTON", L"可选：下载 Whisper large-v3 模型（默认关闭）",
                                         BS_AUTOCHECKBOX, setup_window_, IdSetupModel);
            setup_cpu_ = CreateControl(L"BUTTON", L"CPU", BS_AUTORADIOBUTTON,
                                       setup_window_, IdSetupCpu);
            setup_cuda_ = CreateControl(L"BUTTON", L"NVIDIA CUDA 12", BS_AUTORADIOBUTTON,
                                        setup_window_, IdSetupCuda);
            setup_mirror_label_ = CreateControl(L"STATIC", L"镜像 Base URL（留空使用官方源）",
                                                SS_LEFT | SS_CENTERIMAGE, setup_window_, 0);
            setup_mirror_ = CreateControl(L"EDIT", L"", ES_AUTOHSCROLL,
                                          setup_window_, IdSetupMirror, WS_EX_CLIENTEDGE);
            setup_progress_ = CreateControl(PROGRESS_CLASSW, L"", 0,
                                            setup_window_, IdSetupProgress);
            setup_log_ = CreateControl(L"EDIT", L"", ES_MULTILINE | ES_AUTOVSCROLL |
                                       ES_READONLY | WS_VSCROLL, setup_window_, IdSetupLog,
                                       WS_EX_CLIENTEDGE);
            setup_install_ = CreateControl(L"BUTTON", L"安装所选", BS_DEFPUSHBUTTON,
                                           setup_window_, IdSetupInstall);
            setup_cancel_ = CreateControl(L"BUTTON", L"取消", BS_PUSHBUTTON,
                                          setup_window_, IdSetupCancel);
            setup_skip_ = CreateControl(L"BUTTON", L"稍后设置", BS_PUSHBUTTON,
                                        setup_window_, IdSetupSkip);
            Button_SetCheck(setup_python_, BST_CHECKED);
            Button_SetCheck(setup_asr_, BST_CHECKED);
            Button_SetCheck(setup_cpu_, BST_CHECKED);
            EnableWindow(setup_cancel_, FALSE);
            LayoutSetupWindow();
            return 0;
        case WM_SIZE:
            LayoutSetupWindow();
            return 0;
        case WM_DPICHANGED: {
            dpi_ = HIWORD(wparam);
            const auto* suggested = reinterpret_cast<RECT*>(lparam);
            SetWindowPos(setup_window_,
                         nullptr,
                         suggested->left,
                         suggested->top,
                         suggested->right - suggested->left,
                         suggested->bottom - suggested->top,
                         SWP_NOACTIVATE | SWP_NOZORDER);
            ApplyUiFont();
            LayoutSetupWindow();
            return 0;
        }
        case WM_COMMAND:
            switch (LOWORD(wparam)) {
                case IdSetupCpu:
                    if (HIWORD(wparam) == BN_CLICKED) {
                        Button_SetCheck(setup_cpu_, BST_CHECKED);
                        Button_SetCheck(setup_cuda_, BST_UNCHECKED);
                    }
                    return 0;
                case IdSetupCuda:
                    if (HIWORD(wparam) == BN_CLICKED) {
                        Button_SetCheck(setup_cuda_, BST_CHECKED);
                        Button_SetCheck(setup_cpu_, BST_UNCHECKED);
                    }
                    return 0;
                case IdSetupInstall:
                    StartBootstrap();
                    return 0;
                case IdSetupCancel:
                    if (bootstrap_client_ != nullptr && bootstrap_client_->Running()) {
                        bootstrap_client_->Cancel();
                        AppendSetupLog(L"已请求取消；临时文件会保留以便下次恢复。 ");
                    } else {
                        CloseSetupWizard();
                    }
                    return 0;
                case IdSetupSkip:
                    CloseSetupWizard();
                    return 0;
                default:
                    break;
            }
            return 0;
        case WM_CLOSE:
            if (bootstrap_client_ != nullptr && bootstrap_client_->Running()) {
                MessageBoxW(setup_window_, L"请先取消当前引导任务。", L"ASMR Translation",
                            MB_ICONINFORMATION);
                return 0;
            }
            CloseSetupWizard();
            return 0;
        default:
            break;
    }
    return DefWindowProcW(setup_window_, message, wparam, lparam);
}

JsonObject Application::ProviderJson(const asmr::ProviderSettings& provider,
                                     const std::wstring& credential) const {
    JsonObject object;
    PutString(object, L"kind", provider.kind);
    PutString(object, L"base_url", provider.base_url);
    PutString(object, L"model", provider.model);
    PutBoolean(object, L"strict_schema", provider.strict_schema);
    PutString(object, L"protocol", provider.protocol);
    if (provider.kind == L"openai") {
        PutString(object, L"api_key", credential);
    }
    return object;
}

JsonObject Application::ConfigJson() const {
    JsonObject config;
    PutString(config, L"asr_model", settings_.asr_model);
    PutString(config, L"cache_root", settings_.cache_root);
    PutString(config, L"ffmpeg_path", settings_.ffmpeg_path);
    PutString(config, L"glossary_path", settings_.glossary_path);
    PutString(config, L"quality_mode", settings_.quality_mode ? L"quality" : L"balanced");
    PutBoolean(config, L"review_enabled", settings_.review_enabled);
    PutBoolean(config, L"overwrite", Button_GetCheck(overwrite_) == BST_CHECKED);
    PutNumber(config, L"batch_size", 12);
    PutNumber(config, L"context_before", 8);
    PutNumber(config, L"context_after", 8);
    PutNumber(config, L"prompt_character_limit", 24000);
    config.SetNamedValue(
        L"draft_provider",
        ProviderJson(settings_.draft, asmr::ReadCredential(kDraftCredential)));
    if (settings_.review_same_as_draft) {
        PutString(config, L"review_provider", L"same");
    } else {
        config.SetNamedValue(
            L"review_provider",
            ProviderJson(settings_.review, asmr::ReadCredential(kReviewCredential)));
    }
    if (settings_.analysis_enabled && !settings_.analysis.model.empty()) {
        config.SetNamedValue(L"analysis_provider", ProviderJson(settings_.analysis, L""));
    }
    if (settings_.fallback_enabled && !settings_.fallback.model.empty()) {
        config.SetNamedValue(L"fallback_provider", ProviderJson(settings_.fallback, L""));
    }
    return config;
}

JsonObject Application::DownloadConfigJson() const {
    JsonObject config;
    PutString(config, L"download_endpoint", settings_.download_endpoint);
    PutString(config, L"curl_path", settings_.curl_path);
    PutString(config, L"download_proxy", settings_.download_proxy);
    PutNumber(config, L"download_connect_timeout", settings_.download_connect_timeout);
    return config;
}

void Application::StartProbe() {
    if (task_worker_->Running()) {
        AppendLog(L"已有任务正在运行，请先等待完成或取消。 ");
        return;
    }
    ReadSettingsFromControls();
    AppendLog(L"正在启动环境探测……");
    JsonObject request;
    PutNumber(request, L"protocol", 1);
    PutString(request, L"command", L"probe");
    request.SetNamedValue(L"config", ConfigJson());
    if (task_worker_->Start(settings_.python_path, std::wstring(request.Stringify()))) {
        EnableWindow(probe_, FALSE);
        EnableWindow(run_, FALSE);
        EnableWindow(cancel_, TRUE);
    } else {
        AppendLog(L"无法启动 Python GUI worker，请检查解释器路径。 ");
    }
}

void Application::StartTask() {
    if (task_worker_->Running()) {
        AppendLog(L"已有任务正在运行，请先等待完成或取消。 ");
        return;
    }
    const auto root = TextOf(folder_);
    if (root.empty() || !std::filesystem::is_directory(root)) {
        AppendLog(L"未开始处理：请选择有效的音频文件夹。 ");
        MessageBoxW(window_, L"请选择有效的音频文件夹。", L"ASMR Translation", MB_ICONWARNING);
        return;
    }
    StartTaskRoot(std::filesystem::path(root));
}

void Application::StartTaskRoot(const std::filesystem::path& root_path) {
    if (task_worker_->Running()) {
        pending_download_tasks_.push_back(root_path);
        AppendLog(L"已有翻译任务运行，下载目录已加入 FIFO 队列。 ");
        return;
    }
    const auto root = root_path.wstring();
    ReadSettingsFromControls();
    JsonObject request;
    PutNumber(request, L"protocol", 1);
    PutString(request, L"command", L"run");
    PutString(request, L"root", root);
    request.SetNamedValue(L"config", ConfigJson());
    ListView_DeleteAllItems(task_list_);
    SendMessageW(task_progress_, PBM_SETPOS, 0, 0);
    if (task_worker_->Start(settings_.python_path, std::wstring(request.Stringify()))) {
        EnableWindow(probe_, FALSE);
        EnableWindow(run_, FALSE);
        EnableWindow(cancel_, TRUE);
        AppendLog(L"任务已启动。 ");
    } else {
        AppendLog(L"无法启动任务 worker。 ");
    }
}

void Application::StartDownloadQuery() {
    if (download_worker_->Running()) {
        AppendLog(L"已有下载请求正在运行，请稍候。 ");
        return;
    }
    const auto rj = TextOf(download_rj_);
    if (rj.empty()) {
        MessageBoxW(window_, L"请输入 RJ 编号或 DLsite 作品链接。", L"ASMR Translation",
                    MB_ICONWARNING);
        return;
    }
    ReadSettingsFromControls();
    download_plan_json_.clear();
    download_file_ids_.clear();
    download_file_audio_.clear();
    download_file_smart_.clear();
    ListView_DeleteAllItems(download_files_);
    if (!settings_.download_notice_shown) {
        const auto answer = MessageBoxW(
            window_,
            L"下载服务：api.asmr-200.com\n\n"
            L"本功能整合 thiliapr/asmr-one-downloader（AGPL-3.0-or-later）。\n"
            L"只下载你有权访问的内容；请遵守作品和服务条款。\n\n"
            L"是否继续查询？",
            L"首次使用下载功能",
            MB_OKCANCEL | MB_ICONINFORMATION);
        if (answer != IDOK) {
            return;
        }
        settings_.download_notice_shown = true;
        try {
            asmr::SaveSettings(settings_);
        } catch (...) {
            // A notice is informational; a read-only settings directory must not
            // prevent the first query.
        }
    }
    JsonObject request;
    PutNumber(request, L"protocol", 1);
    PutString(request, L"command", L"download_plan");
    PutString(request, L"rj", rj);
    request.SetNamedValue(L"download", DownloadConfigJson());
    if (!download_worker_->Start(settings_.python_path, std::wstring(request.Stringify()))) {
        AppendDownloadLog(L"无法启动下载查询 worker，请检查 Python 路径。 ");
        return;
    }
    EnableWindow(download_query_, FALSE);
    EnableWindow(download_run_, FALSE);
    EnableWindow(download_cancel_, TRUE);
    AppendDownloadLog(L"正在查询作品和文件清单……");
}

void Application::StartDownloadRun() {
    if (download_worker_->Running()) {
        AppendLog(L"已有下载请求正在运行，请稍候。 ");
        return;
    }
    if (download_plan_json_.empty()) {
        MessageBoxW(window_, L"请先查询作品。", L"ASMR Translation", MB_ICONWARNING);
        return;
    }
    ReadSettingsFromControls();
    const auto root = TextOf(download_root_);
    if (root.empty()) {
        MessageBoxW(window_, L"请选择下载资料库目录。", L"ASMR Translation", MB_ICONWARNING);
        return;
    }
    JsonArray selected;
    for (std::size_t index = 0; index < download_file_ids_.size(); ++index) {
        if (ListView_GetCheckState(download_files_, static_cast<int>(index))) {
            selected.Append(JsonValue::CreateStringValue(download_file_ids_[index]));
        }
    }
    if (selected.Size() == 0) {
        MessageBoxW(window_, L"请至少勾选一个文件。", L"ASMR Translation", MB_ICONWARNING);
        return;
    }
    JsonObject request;
    PutNumber(request, L"protocol", 1);
    PutString(request, L"command", L"download_run");
    request.SetNamedValue(L"plan", JsonObject::Parse(download_plan_json_));
    request.SetNamedValue(L"selected_ids", selected);
    PutString(request, L"output_root", root);
    request.SetNamedValue(L"download", DownloadConfigJson());
    if (!download_worker_->Start(settings_.python_path, std::wstring(request.Stringify()))) {
        AppendDownloadLog(L"无法启动下载 worker。 ");
        return;
    }
    EnableWindow(download_query_, FALSE);
    EnableWindow(download_run_, FALSE);
    EnableWindow(download_cancel_, TRUE);
    SendMessageW(download_progress_, PBM_SETRANGE32, 0, 1000);
    SendMessageW(download_progress_, PBM_SETPOS, 0, 0);
    AppendDownloadLog(L"下载任务已启动；已完成文件会跳过，.part 文件会续传。 ");
}

void Application::QueueDownloadedTask(const std::filesystem::path& root) {
    if (!Button_GetCheck(download_auto_)) {
        AppendLog(L"下载完成：" + root.wstring());
        return;
    }
    if (task_worker_->Running()) {
        if (std::ranges::find(pending_download_tasks_, root) == pending_download_tasks_.end()) {
            pending_download_tasks_.push_back(root);
            AppendLog(L"翻译任务忙，下载目录已排入 FIFO 队列。 ");
        }
        return;
    }
    SetText(folder_, root.wstring());
    StartTaskRoot(root);
}

void Application::SetDownloadSelection(const int mode) {
    for (std::size_t index = 0; index < download_file_ids_.size(); ++index) {
        bool checked = false;
        if (mode == 0) {
            checked = index < download_file_smart_.size() && download_file_smart_[index];
        } else if (mode == 1) {
            checked = index < download_file_audio_.size() && download_file_audio_[index];
        } else if (mode == 2) {
            checked = true;
        }
        ListView_SetCheckState(download_files_, static_cast<int>(index), checked);
    }
}

void Application::OpenAudio(const std::filesystem::path& path) {
    if (!std::filesystem::is_regular_file(path)) {
        return;
    }
    current_audio_ = std::filesystem::absolute(path);
    SetWindowTextW(window_, (L"ASMR Translation — " + current_audio_.filename().wstring()).c_str());
    lyrics_.SetCues(asmr::ParseLrc(current_audio_.replace_extension(L".lrc")));
    current_audio_ = std::filesystem::absolute(path);
    playback_proxy_pending_ = false;
    StartLoadCues();
    if (!player_.Open(current_audio_)) {
        SetText(time_, L"无法打开音频");
        AppendLog(std::format(
            L"Media Foundation 无法打开音频（即时 HRESULT 0x{:08X}），正在尝试 FFmpeg 播放代理。",
            static_cast<unsigned long>(player_.OpenError())));
        StartPlaybackProxy();
    }
    const auto found = std::ranges::find(playlist_, current_audio_);
    if (found == playlist_.end()) {
        playlist_.push_back(current_audio_);
        playlist_index_ = playlist_.size() - 1;
    } else {
        playlist_index_ = static_cast<std::size_t>(std::distance(playlist_.begin(), found));
    }
}

void Application::StartLoadCues() {
    if (current_audio_.empty() || utility_worker_->Running()) {
        return;
    }
    JsonObject request;
    PutNumber(request, L"protocol", 1);
    PutString(request, L"command", L"load_cues");
    PutString(request, L"audio", current_audio_.wstring());
    PutString(request, L"cache_root", settings_.cache_root);
    utility_action_ = UtilityAction::LoadCues;
    if (!utility_worker_->Start(settings_.python_path, std::wstring(request.Stringify()))) {
        utility_action_ = UtilityAction::None;
        AppendLog(L"无法启动台词缓存 worker。");
    }
}

void Application::StartPlaybackProxy() {
    if (current_audio_.empty()) {
        return;
    }
    if (utility_worker_->Running()) {
        playback_proxy_pending_ = true;
        return;
    }
    JsonObject request;
    PutNumber(request, L"protocol", 1);
    PutString(request, L"command", L"prepare_playback");
    PutString(request, L"audio", current_audio_.wstring());
    PutString(request, L"cache_root", settings_.cache_root);
    PutString(request, L"ffmpeg_path", settings_.ffmpeg_path);
    PutNumber(request, L"limit_bytes", 4.0 * 1024 * 1024 * 1024);
    utility_action_ = UtilityAction::PreparePlayback;
    playback_proxy_pending_ = false;
    if (utility_worker_->Start(settings_.python_path, std::wstring(request.Stringify()))) {
        AppendLog(L"系统解码器不支持该格式，正在生成无损 PCM 播放代理。 ");
    } else {
        utility_action_ = UtilityAction::None;
        SetText(time_, L"无法启动播放代理");
        AppendLog(L"无法启动 FFmpeg 播放代理 worker，请检查 Python 路径。");
    }
}

void Application::SaveLyric() {
    const auto selected = lyrics_.Selected();
    if (!selected || *selected >= lyrics_.Cues().size() || utility_worker_->Running()) {
        return;
    }
    const auto text = TextOf(lyric_edit_);
    if (text.empty()) {
        MessageBoxW(window_, L"台词不能为空。", L"ASMR Translation", MB_ICONWARNING);
        return;
    }
    JsonObject request;
    PutNumber(request, L"protocol", 1);
    PutString(request, L"command", L"save_edits");
    PutString(request, L"audio", current_audio_.wstring());
    PutString(request, L"cache_root", settings_.cache_root);
    JsonObject edit;
    PutString(edit, L"id", lyrics_.Cues()[*selected].id);
    PutString(edit, L"text", text);
    JsonArray edits;
    edits.Append(edit);
    request.SetNamedValue(L"edits", edits);
    utility_action_ = UtilityAction::SaveEdits;
    if (utility_worker_->Start(settings_.python_path, std::wstring(request.Stringify()))) {
        pending_edit_ = std::pair{*selected, text};
    }
}

void Application::HandleDownloadMetadata(const JsonObject& event) {
    const auto plan = event.GetNamedObject(L"plan");
    download_plan_json_ = std::wstring(plan.Stringify());
    download_file_ids_.clear();
    download_file_audio_.clear();
    download_file_smart_.clear();
    ListView_DeleteAllItems(download_files_);
    std::vector<std::wstring> smart_ids;
    if (plan.HasKey(L"smart_selected_ids")) {
        for (const auto& value : plan.GetNamedArray(L"smart_selected_ids")) {
            smart_ids.emplace_back(value.GetString());
        }
    }
    const auto files = plan.GetNamedArray(L"files");
    int row = 0;
    for (const auto& value : files) {
        const auto item = value.GetObject();
        const auto id = std::wstring(item.GetNamedString(L"id"));
        const auto path = std::wstring(item.GetNamedString(L"path"));
        const auto kind = std::wstring(item.GetNamedString(L"type", L"file"));
        const bool audio = IsAudioFile(path, kind);
        const bool smart = std::ranges::find(smart_ids, id) != smart_ids.end();
        const auto size = static_cast<unsigned long long>(item.GetNamedNumber(L"size", 0));
        const auto size_text = std::format(L"{} MB", (size + 999999) / 1000000);
        LVITEMW list_item{};
        list_item.mask = LVIF_TEXT;
        list_item.iItem = row;
        list_item.pszText = const_cast<wchar_t*>(kind.c_str());
        ListView_InsertItem(download_files_, &list_item);
        ListView_SetItemText(download_files_, row, 1, const_cast<wchar_t*>(path.c_str()));
        ListView_SetItemText(download_files_, row, 2, const_cast<wchar_t*>(size_text.c_str()));
        ListView_SetCheckState(download_files_, row, smart);
        download_file_ids_.push_back(id);
        download_file_audio_.push_back(audio);
        download_file_smart_.push_back(smart);
        ++row;
    }
    const auto title = std::wstring(plan.GetNamedString(L"title", L""));
    const auto circle = std::wstring(plan.GetNamedString(L"circle", L""));
    const auto source = std::wstring(plan.GetNamedString(L"source_id", L""));
    const auto summary = std::format(L"作品查询完成：{} [{}] [{}]，{} 个文件，共 {} MB。",
                                     title,
                                     source,
                                     circle,
                                     row,
                                     (static_cast<unsigned long long>(plan.GetNamedNumber(L"total_size", 0)) +
                                      999999) /
                                         1000000);
    AppendDownloadLog(summary);
    EnableWindow(download_run_, row > 0);
}

void Application::HandlePlan(const JsonObject& event) {
    playlist_.clear();
    const auto items = event.GetNamedArray(L"items");
    int row = 0;
    for (const auto& value : items) {
        const auto item = value.GetObject();
        const auto audio = std::wstring(item.GetNamedString(L"audio"));
        const auto action = std::wstring(item.GetNamedString(L"action"));
        LVITEMW list_item{};
        list_item.mask = LVIF_TEXT;
        list_item.iItem = row;
        list_item.pszText = const_cast<wchar_t*>(action.c_str());
        ListView_InsertItem(task_list_, &list_item);
        ListView_SetItemText(task_list_, row, 1, const_cast<wchar_t*>(audio.c_str()));
        playlist_.emplace_back(audio);
        ++row;
    }
}

void Application::HandleCues(const JsonObject& event) {
    std::vector<asmr::Cue> cues;
    for (const auto& value : event.GetNamedArray(L"cues")) {
        const auto item = value.GetObject();
        asmr::Cue cue;
        cue.id = std::wstring(item.GetNamedString(L"id"));
        cue.start = item.GetNamedNumber(L"start");
        cue.end = item.HasKey(L"end") && item.GetNamedValue(L"end").ValueType() == JsonValueType::Number
                      ? item.GetNamedNumber(L"end")
                      : cue.start;
        cue.source = std::wstring(item.GetNamedString(L"source", L""));
        cue.text = std::wstring(item.GetNamedString(L"text", L""));
        if (item.HasKey(L"flags")) {
            for (const auto& flag : item.GetNamedArray(L"flags")) {
                cue.flags.emplace_back(flag.GetString());
            }
        }
        cues.push_back(std::move(cue));
    }
    lyrics_.SetCues(std::move(cues));
}

void Application::HandleWorkerEvent(const WorkerChannel channel, const std::wstring& json) {
    try {
        const auto envelope = asmr::ParseWorkerEventEnvelope(json);
        const auto event = JsonObject::Parse(json);
        const auto& name = envelope.event;
        if (channel == WorkerChannel::Download && name == L"download_metadata") {
            HandleDownloadMetadata(event);
        } else if (channel == WorkerChannel::Download && name == L"download_progress") {
            const auto current = event.GetNamedNumber(L"size", 0);
            const auto total = std::max(1.0, event.GetNamedNumber(L"total", 1));
            SendMessageW(download_progress_, PBM_SETPOS,
                         static_cast<WPARAM>(std::clamp(current / total * 1000.0, 0.0, 1000.0)), 0);
        } else if (channel == WorkerChannel::Download && name == L"download_file") {
            const auto status = std::wstring(event.GetNamedString(L"status", L""));
            const auto path = std::wstring(event.GetNamedString(L"path", L""));
            if (status == L"completed" || status == L"skipped") {
                AppendDownloadLog(L"下载文件 " + status + L"：" + path);
            }
        } else if (channel == WorkerChannel::Download && name == L"download_retry") {
            const auto line = std::format(L"下载重试 {}（第 {} 次，等待 {} 秒）。",
                                          std::wstring(event.GetNamedString(L"path", L"")),
                                          static_cast<int>(event.GetNamedNumber(L"attempt", 0)),
                                          static_cast<int>(event.GetNamedNumber(L"delay", 0)));
            AppendDownloadLog(line);
        } else if (channel == WorkerChannel::Download && name == L"download_complete") {
            const auto root = std::filesystem::path(std::wstring(event.GetNamedString(L"root")));
            SendMessageW(download_progress_, PBM_SETPOS, 1000, 0);
            AppendDownloadLog(L"下载完成：" + root.wstring());
            QueueDownloadedTask(root);
        } else if (name == L"log") {
            AppendLog(std::wstring(event.GetNamedString(L"message")));
        } else if (name == L"plan") {
            HandlePlan(event);
        } else if (name == L"phase" || name == L"batch") {
            const auto current = static_cast<int>(event.GetNamedNumber(L"current", 0));
            const auto total = static_cast<int>(event.GetNamedNumber(L"total", 1));
            SendMessageW(task_progress_, PBM_SETRANGE32, 0, std::max(1, total));
            SendMessageW(task_progress_, PBM_SETPOS, current, 0);
        } else if (name == L"probe_result") {
            AppendLog(L"环境探测完成：" + std::wstring(event.GetNamedObject(L"result").Stringify()));
        } else if (name == L"cues") {
            HandleCues(event);
        } else if (name == L"playback_ready") {
            const auto proxy = std::filesystem::path(std::wstring(event.GetNamedString(L"path")));
            if (player_.Open(proxy)) {
                AppendLog(L"PCM 播放代理已就绪。");
            } else {
                SetText(time_, L"无法打开播放代理");
                AppendLog(std::format(L"PCM 播放代理无法打开（即时 HRESULT 0x{:08X}）。",
                                      static_cast<unsigned long>(player_.OpenError())));
            }
        } else if (name == L"saved") {
            if (pending_edit_ && pending_edit_->first < lyrics_.Cues().size()) {
                auto cues = lyrics_.Cues();
                cues[pending_edit_->first].text = pending_edit_->second;
                if (std::ranges::find(cues[pending_edit_->first].flags, L"manual_edited") ==
                    cues[pending_edit_->first].flags.end()) {
                    cues[pending_edit_->first].flags.emplace_back(L"manual_edited");
                }
                const auto edited_index = pending_edit_->first;
                lyrics_.SetCues(std::move(cues));
                lyrics_.SetActive(edited_index);
                pending_edit_.reset();
            }
            AppendLog(L"台词已原子保存；首次备份位于 " +
                      std::wstring(event.GetNamedString(L"backup")));
        } else if (name == L"external_consent_required") {
            const auto characters =
                static_cast<unsigned long long>(event.GetNamedNumber(L"estimated_characters"));
            const auto message = std::format(
                L"本任务预计向外部 API 发送约 {} 个转写文本字符。\n\n"
                L"音频文件不会上传；API Key 不会写入配置、日志或命令行。是否继续？",
                characters);
            const bool approved =
                MessageBoxW(window_, message.c_str(), L"外部 API 授权", MB_YESNO | MB_ICONWARNING) ==
                IDYES;
            task_worker_->SendControl(approved
                                          ? LR"({"protocol":1,"command":"consent","approved":true})"
                                          : LR"({"protocol":1,"command":"consent","approved":false})");
        } else if (name == L"error") {
            if (channel == WorkerChannel::Utility) {
                pending_edit_.reset();
            }
            if (channel == WorkerChannel::Download) {
                AppendDownloadLog(L"错误：" + std::wstring(event.GetNamedString(L"message")));
            }
            AppendLog(L"错误：" + std::wstring(event.GetNamedString(L"message")));
        } else if (name == L"cancelled") {
            AppendLog(L"任务已取消。 ");
        } else if (name == L"result") {
            AppendLog(L"任务完成。 ");
        }
    } catch (...) {
        AppendLog(L"worker 返回了无法解析的消息：" + json);
    }
    (void)channel;
}

void Application::HandleWorkerDone(const WorkerChannel channel, const DWORD exit_code) {
    if (channel == WorkerChannel::Task) {
        EnableWindow(probe_, TRUE);
        EnableWindow(run_, TRUE);
        EnableWindow(cancel_, FALSE);
        KillTimer(window_, kCancelTimer);
        AppendLog(std::format(L"任务 worker 退出码：{}", exit_code));
        if (!pending_download_tasks_.empty()) {
            const auto next = pending_download_tasks_.front();
            pending_download_tasks_.pop_front();
            SetText(folder_, next.wstring());
            StartTaskRoot(next);
        }
    } else if (channel == WorkerChannel::Download) {
        EnableWindow(download_query_, TRUE);
        EnableWindow(download_run_, !download_plan_json_.empty());
        EnableWindow(download_cancel_, FALSE);
        KillTimer(window_, kDownloadCancelTimer);
        if (exit_code != 0) {
            const auto message = std::format(L"下载 worker 退出码：{}", exit_code);
            AppendDownloadLog(message);
            AppendLog(message);
        }
    } else {
        if (exit_code != 0) {
            AppendLog(std::format(L"实用 worker 退出码：{}", exit_code));
        }
        if (utility_action_ == UtilityAction::SaveEdits) {
            pending_edit_.reset();
        }
        utility_action_ = UtilityAction::None;
        if (playback_proxy_pending_) {
            StartPlaybackProxy();
        }
    }
}

void Application::UpdatePlayer() {
    if (!player_.Ready()) {
        return;
    }
    const auto position = player_.Position();
    const auto duration = player_.Duration();
    if (!seek_dragging_ && duration > 0) {
        const auto value = static_cast<int>(std::clamp(position / duration, 0.0, 1.0) * 10000);
        SendMessageW(seek_, TBM_SETPOS, TRUE, value);
    }
    SetText(time_, FormatTime(position) + L" / " + FormatTime(duration));
    SetText(play_, player_.Paused() ? L"播放" : L"暂停");
    lyrics_.SetActive(asmr::ActiveCueIndex(lyrics_.Cues(), position));
}

void Application::NavigatePlaylist(const int direction) {
    if (playlist_.empty()) {
        return;
    }
    auto index = playlist_index_.value_or(0);
    if (direction < 0) {
        index = index == 0 ? playlist_.size() - 1 : index - 1;
    } else {
        index = (index + 1) % playlist_.size();
    }
    playlist_index_ = index;
    OpenAudio(playlist_[index]);
}

LRESULT Application::HandleMessage(const UINT message, const WPARAM wparam, const LPARAM lparam) {
    switch (message) {
        case WM_CREATE:
            dpi_ = GetDpiForWindow(window_);
            settings_ = asmr::LoadSettings();
            CreatePages();
            lyrics_.SetDpi(dpi_);
            ApplyUiFont();
            DragAcceptFiles(window_, TRUE);
            LoadSettingsIntoControls();
            task_worker_ = std::make_unique<asmr::WorkerClient>(window_, WorkerChannel::Task);
            utility_worker_ = std::make_unique<asmr::WorkerClient>(window_, WorkerChannel::Utility);
            download_worker_ = std::make_unique<asmr::WorkerClient>(window_, WorkerChannel::Download);
            bootstrap_client_ = std::make_unique<asmr::BootstrapClient>(window_);
            if (player_.Initialize(window_)) {
                player_.SetVolume(0.8);
            } else {
                SetText(time_, L"播放器初始化失败");
                AppendLog(L"Media Foundation 播放器初始化失败。 ");
            }
            SetTimer(window_, kPlayerTimer, 50, nullptr);
            PostMessageW(window_, WM_APP_SETUP_SHOW, 0, 0);
            return 0;
        case WM_SIZE:
            Layout();
            return 0;
        case WM_DPICHANGED: {
            dpi_ = HIWORD(wparam);
            lyrics_.SetDpi(dpi_);
            ApplyUiFont();
            const auto* suggested = reinterpret_cast<RECT*>(lparam);
            SetWindowPos(window_,
                         nullptr,
                         suggested->left,
                         suggested->top,
                         suggested->right - suggested->left,
                         suggested->bottom - suggested->top,
                         SWP_NOACTIVATE | SWP_NOZORDER);
            Layout();
            return 0;
        }
        case WM_DROPFILES: {
            const auto drop = reinterpret_cast<HDROP>(wparam);
            const auto length = DragQueryFileW(drop, 0, nullptr, 0);
            std::wstring path(static_cast<std::size_t>(length) + 1, L'\0');
            if (length > 0) {
                DragQueryFileW(drop, 0, path.data(), length + 1);
                path.resize(length);
                OpenOnStartup(path);
            }
            DragFinish(drop);
            return 0;
        }
        case WM_NOTIFY:
            if (reinterpret_cast<NMHDR*>(lparam)->hwndFrom == tab_ &&
                reinterpret_cast<NMHDR*>(lparam)->code == TCN_SELCHANGE) {
                SelectPage();
            } else if (reinterpret_cast<NMHDR*>(lparam)->hwndFrom == task_list_ &&
                       reinterpret_cast<NMHDR*>(lparam)->code == NM_DBLCLK) {
                const auto row = ListView_GetNextItem(task_list_, -1, LVNI_SELECTED);
                if (row >= 0 && row < static_cast<int>(playlist_.size())) {
                    OpenAudio(playlist_[static_cast<std::size_t>(row)]);
                    TabCtrl_SetCurSel(tab_, 1);
                    SelectPage();
                }
            }
            return 0;
        case WM_COMMAND: {
            const auto id = LOWORD(wparam);
            if (id == IdBrowseFolder) {
                if (const auto folder = PickFolder(window_)) {
                    SetText(folder_, folder->wstring());
                }
            } else if (id == IdProbe) {
                StartProbe();
            } else if (id == IdRun) {
                StartTask();
            } else if (id == IdCancel) {
                task_worker_->Cancel();
                SetTimer(window_, kCancelTimer, 5000, nullptr);
                AppendLog(L"已请求协作取消；5 秒后仍未退出将清理整个任务进程树。 ");
            } else if (id == IdDownloadBrowse) {
                if (const auto folder = PickFolder(window_)) {
                    SetText(download_root_, folder->wstring());
                    settings_.download_root = folder->wstring();
                }
            } else if (id == IdDownloadQuery) {
                StartDownloadQuery();
            } else if (id == IdDownloadRun) {
                StartDownloadRun();
            } else if (id == IdDownloadCancel) {
                download_worker_->Cancel();
                SetTimer(window_, kDownloadCancelTimer, 5000, nullptr);
                AppendLog(L"已请求取消下载；5 秒后仍未退出将清理 curl/Python 进程树。 ");
            } else if (id == IdDownloadSmart) {
                SetDownloadSelection(0);
            } else if (id == IdDownloadAudio) {
                SetDownloadSelection(1);
            } else if (id == IdDownloadAll) {
                SetDownloadSelection(2);
            } else if (id == IdDownloadNone) {
                SetDownloadSelection(3);
            } else if (id == IdDownloadAbout) {
                ShellExecuteW(window_, L"open", kProjectUrl, nullptr, nullptr, SW_SHOWNORMAL);
            } else if (id == IdOpenAudio) {
                if (const auto audio = PickAudio(window_)) {
                    OpenAudio(*audio);
                }
            } else if (id == IdPlay) {
                player_.PlayPause();
            } else if (id == IdPrevious) {
                NavigatePlaylist(-1);
            } else if (id == IdNext) {
                NavigatePlaylist(1);
            } else if (id == IdSpeed && HIWORD(wparam) == CBN_SELCHANGE) {
                constexpr double rates[] = {0.75, 1.0, 1.25, 1.5, 2.0};
                const auto selected = ComboBox_GetCurSel(speed_);
                if (selected >= 0 && selected < static_cast<int>(std::size(rates))) {
                    player_.SetRate(rates[selected]);
                }
            } else if (
                (id == IdDraftKind || id == IdReviewKind) &&
                HIWORD(wparam) == CBN_SELCHANGE) {
                UpdateSettingsEnabled();
            } else if (id == IdReviewSame && HIWORD(wparam) == BN_CLICKED) {
                UpdateSettingsEnabled();
            } else if (id == IdLyricSave) {
                SaveLyric();
            } else if (id == IdSaveSettings) {
                try {
                    ReadSettingsFromControls();
                    asmr::WriteCredential(kDraftCredential, TextOf(draft_key_));
                    asmr::WriteCredential(kReviewCredential, TextOf(review_key_));
                    asmr::SaveSettings(settings_);
                    MessageBoxW(window_, L"设置已保存。", L"ASMR Translation", MB_ICONINFORMATION);
                } catch (const std::exception& error) {
                    MessageBoxW(window_, asmr::Utf8ToWide(error.what()).c_str(), L"保存失败", MB_ICONERROR);
                }
            } else if (id == IdSetupOpen) {
                ShowSetupWizard();
            }
            return 0;
        }
        case WM_HSCROLL:
            if (reinterpret_cast<HWND>(lparam) == seek_) {
                const auto notification = LOWORD(wparam);
                seek_dragging_ = notification == TB_THUMBTRACK;
                if (notification == TB_ENDTRACK || notification == TB_THUMBPOSITION ||
                    notification == TB_PAGEUP || notification == TB_PAGEDOWN) {
                    const auto value = SendMessageW(seek_, TBM_GETPOS, 0, 0);
                    player_.Seek(player_.Duration() * static_cast<double>(value) / 10000.0);
                    seek_dragging_ = false;
                }
            } else if (reinterpret_cast<HWND>(lparam) == volume_) {
                player_.SetVolume(
                    static_cast<double>(SendMessageW(volume_, TBM_GETPOS, 0, 0)) / 100.0);
            }
            return 0;
        case WM_VSCROLL:
            if (lparam == 0 && TabCtrl_GetCurSel(tab_) == 3) {
                SCROLLINFO scroll_info{sizeof(SCROLLINFO), SIF_ALL};
                GetScrollInfo(settings_page_, SB_VERT, &scroll_info);
                int position = scroll_info.nPos;
                switch (LOWORD(wparam)) {
                    case SB_LINEUP:
                        position -= MulDiv(32, static_cast<int>(dpi_), 96);
                        break;
                    case SB_LINEDOWN:
                        position += MulDiv(32, static_cast<int>(dpi_), 96);
                        break;
                    case SB_PAGEUP:
                        position -= static_cast<int>(scroll_info.nPage);
                        break;
                    case SB_PAGEDOWN:
                        position += static_cast<int>(scroll_info.nPage);
                        break;
                    case SB_THUMBTRACK:
                    case SB_THUMBPOSITION:
                        position = scroll_info.nTrackPos;
                        break;
                    default:
                        break;
                }
                const int maximum = std::max(
                    0,
                    scroll_info.nMax - static_cast<int>(scroll_info.nPage) + 1);
                settings_scroll_ = std::clamp(position, 0, maximum);
                SetScrollPos(settings_page_, SB_VERT, settings_scroll_, TRUE);
                Layout();
            }
            return 0;
        case WM_TIMER:
            if (wparam == kPlayerTimer) {
                UpdatePlayer();
            } else if (wparam == kCancelTimer) {
                KillTimer(window_, kCancelTimer);
                if (task_worker_->Running()) {
                    task_worker_->ForceTerminate();
                }
            } else if (wparam == kDownloadCancelTimer) {
                KillTimer(window_, kDownloadCancelTimer);
                if (download_worker_->Running()) {
                    download_worker_->ForceTerminate();
                }
            }
            return 0;
        case WM_APP_WORKER_EVENT: {
            std::unique_ptr<std::wstring> payload(reinterpret_cast<std::wstring*>(lparam));
            HandleWorkerEvent(static_cast<WorkerChannel>(wparam), *payload);
            return 0;
        }
        case WM_APP_WORKER_DONE:
            HandleWorkerDone(static_cast<WorkerChannel>(wparam), static_cast<DWORD>(lparam));
            return 0;
        case WM_APP_MEDIA_EVENT:
            player_.HandleEvent(static_cast<DWORD>(wparam));
            if (player_.SourceUnsupported()) {
                if (player_.Source() == current_audio_) {
                    AppendLog(std::format(L"系统解码失败（媒体错误 {}，HRESULT 0x{:08X}）。",
                                          player_.ErrorCode(),
                                          static_cast<unsigned long>(player_.ExtendedError())));
                    StartPlaybackProxy();
                } else {
                    SetText(time_, L"播放代理也无法解码");
                    AppendLog(L"PCM WAV 播放代理仍无法由 Media Foundation 播放。 ");
                }
            }
            return 0;
        case WM_APP_SETUP_SHOW:
            MaybeShowSetupWizard();
            return 0;
        case WM_APP_BOOTSTRAP_EVENT: {
            std::unique_ptr<std::wstring> payload(reinterpret_cast<std::wstring*>(lparam));
            HandleBootstrapEvent(*payload);
            return 0;
        }
        case WM_APP_BOOTSTRAP_DONE:
            HandleBootstrapDone(static_cast<DWORD>(lparam));
            return 0;
        case WM_APP_LYRIC_CLICK: {
            const auto index = static_cast<std::size_t>(wparam);
            if (index < lyrics_.Cues().size()) {
                player_.Seek(lyrics_.Cues()[index].start);
            }
            return 0;
        }
        case WM_APP_LYRIC_EDIT: {
            const auto index = static_cast<std::size_t>(wparam);
            if (index < lyrics_.Cues().size()) {
                SetText(lyric_edit_, lyrics_.Cues()[index].text);
                SetFocus(lyric_edit_);
                SendMessageW(lyric_edit_, EM_SETSEL, 0, -1);
            }
            return 0;
        }
        case WM_KEYDOWN:
            if (wparam == VK_SPACE && GetFocus() != lyric_edit_) {
                player_.PlayPause();
                return 0;
            }
            break;
        case WM_CLOSE:
            if ((task_worker_ && task_worker_->Running()) ||
                (utility_worker_ && utility_worker_->Running()) ||
                (download_worker_ && download_worker_->Running()) ||
                (bootstrap_client_ && bootstrap_client_->Running())) {
                const auto answer = MessageBoxW(window_,
                                                L"仍有任务运行。关闭会终止任务进程树，是否继续？",
                                                L"ASMR Translation",
                                                MB_YESNO | MB_ICONWARNING);
                if (answer != IDYES) {
                    return 0;
                }
            }
            DestroyWindow(window_);
            return 0;
        case WM_DESTROY:
            KillTimer(window_, kPlayerTimer);
            task_worker_.reset();
            utility_worker_.reset();
            download_worker_.reset();
            bootstrap_client_.reset();
            if (setup_window_ != nullptr) {
                EnableWindow(window_, TRUE);
                DestroyWindow(setup_window_);
                setup_window_ = nullptr;
            }
            if (ui_font_ != nullptr) {
                DeleteObject(ui_font_);
                ui_font_ = nullptr;
            }
            PostQuitMessage(0);
            return 0;
        default:
            break;
    }
    return DefWindowProcW(window_, message, wparam, lparam);
}

}  // namespace

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int) {
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    winrt::init_apartment(winrt::apartment_type::single_threaded);
    INITCOMMONCONTROLSEX controls{sizeof(INITCOMMONCONTROLSEX),
                                  ICC_STANDARD_CLASSES | ICC_TAB_CLASSES | ICC_LISTVIEW_CLASSES |
                                      ICC_PROGRESS_CLASS | ICC_BAR_CLASSES};
    InitCommonControlsEx(&controls);
    Application app(instance);
    if (!app.Create()) {
        return 2;
    }
    int argument_count = 0;
    auto** arguments = CommandLineToArgvW(GetCommandLineW(), &argument_count);
    if (arguments != nullptr) {
        if (argument_count > 1) {
            app.OpenOnStartup(arguments[1]);
        }
        LocalFree(arguments);
    }
    MSG message{};
    while (GetMessageW(&message, nullptr, 0, 0) > 0) {
        if (message.message == WM_KEYDOWN && message.wParam == VK_SPACE) {
            wchar_t class_name[32]{};
            const auto focus = GetFocus();
            if (focus == nullptr ||
                GetClassNameW(focus, class_name, static_cast<int>(std::size(class_name))) == 0 ||
                _wcsicmp(class_name, L"Edit") != 0) {
                SendMessageW(app.Window(), WM_COMMAND, IdPlay, 0);
                continue;
            }
        }
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
    return static_cast<int>(message.wParam);
}
