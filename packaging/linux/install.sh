#!/usr/bin/env bash
# Per-user installer for the ASMR Translation Qt GUI on Linux.
#
# Everything lands under $XDG_DATA_HOME (or ~/.local/share): a private venv, a
# desktop entry and an icon.  No sudo, no system-wide writes, no files outside
# the user's home.  Distribution packages (ffmpeg, PortAudio, the Qt xcb
# dependencies) are *checked* but never installed for you -- see README.md for
# the per-distro command.
#
# Models are never downloaded here either.  The pipeline refuses to swap models
# silently, so the ASR weights and the Ollama models stay an explicit step you
# run yourself after installing.

set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir/../.." && pwd)

data_home=${XDG_DATA_HOME:-$HOME/.local/share}
prefix=$data_home/asmr-translation
applications_dir=$data_home/applications
icons_dir=$data_home/icons/hicolor/scalable/apps
bin_dir=$HOME/.local/bin

want_cuda=0
want_desktop=1
do_uninstall=0

usage() {
    cat <<'EOF'
Usage: install.sh [options]

  --cuda          also install the CUDA runtime wheels (needs an NVIDIA driver)
  --prefix DIR    install the venv into DIR instead of
                  ${XDG_DATA_HOME:-~/.local/share}/asmr-translation
  --no-desktop    skip the desktop entry, the icon and the ~/.local/bin symlink
  --uninstall     remove what this script installed (settings and caches stay)
  -h, --help      show this message

Environment:
  ASMR_PYTHON     interpreter to build the venv with (default: python3.13,
                  python3.12 or python3, whichever is 3.12/3.13 first)
  PIP_INDEX_URL   honoured as usual by pip, e.g. for a local mirror
EOF
}

while [ $# -gt 0 ]; do
    case $1 in
        --cuda) want_cuda=1 ;;
        --no-desktop) want_desktop=0 ;;
        --uninstall) do_uninstall=1 ;;
        --prefix)
            if [ $# -lt 2 ]; then
                echo "install.sh: --prefix needs a directory" >&2
                exit 2
            fi
            prefix=$2
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "install.sh: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

# The desktop entry needs an absolute Exec, so anchor a relative --prefix now.
case $prefix in
    /*) ;;
    *) prefix=$PWD/$prefix ;;
esac

venv=$prefix/venv
launcher=$venv/bin/asmr-translation
desktop_file=$applications_dir/asmr-translation.desktop
icon_file=$icons_dir/asmr-translation.svg
symlink=$bin_dir/asmr-translation

note() { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die() {
    printf '\033[31merror:\033[0m %s\n' "$*" >&2
    exit 1
}

refresh_caches() {
    # Both are conveniences; a stale cache only delays the menu entry appearing.
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$applications_dir" >/dev/null 2>&1 || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache --force --quiet "$data_home/icons/hicolor" >/dev/null 2>&1 || true
    fi
}

if [ "$do_uninstall" = 1 ]; then
    if [ -d "$venv" ]; then
        note "Removing $venv"
        rm -rf -- "$venv"
    fi
    rmdir -- "$prefix" 2>/dev/null || true
    for path in "$desktop_file" "$icon_file"; do
        if [ -e "$path" ]; then
            note "Removing $path"
            rm -f -- "$path"
        fi
    done
    if [ -L "$symlink" ]; then
        note "Removing $symlink"
        rm -f -- "$symlink"
    fi
    refresh_caches
    printf '\n'
    note "Done.  Settings, caches and logs were left alone:"
    echo "      ${XDG_CONFIG_HOME:-$HOME/.config}/asmr-translation"
    echo "      ${XDG_CACHE_HOME:-$HOME/.cache}/asmr-translation"
    echo "      ${XDG_STATE_HOME:-$HOME/.local/state}/asmr-translation"
    exit 0
fi

if [ "$(id -u)" = 0 ]; then
    die "run this as your normal user, not root -- the install is per-user."
fi
if [ ! -f "$repo_root/pyproject.toml" ]; then
    die "cannot find pyproject.toml above $script_dir."
fi

# --- interpreter -------------------------------------------------------------

supported_python() {
    "$1" -c 'import sys; raise SystemExit(0 if (3, 12) <= sys.version_info < (3, 14) else 1)' \
        >/dev/null 2>&1
}

python=
if [ -n "${ASMR_PYTHON:-}" ]; then
    command -v "$ASMR_PYTHON" >/dev/null 2>&1 \
        || die "ASMR_PYTHON=$ASMR_PYTHON is not executable."
    supported_python "$ASMR_PYTHON" \
        || die "ASMR_PYTHON=$ASMR_PYTHON is not Python 3.12 or 3.13."
    python=$ASMR_PYTHON
else
    for candidate in python3.13 python3.12 python3; do
        if command -v "$candidate" >/dev/null 2>&1 && supported_python "$candidate"; then
            python=$candidate
            break
        fi
    done
    if [ -z "$python" ]; then
        die "no Python 3.12 or 3.13 found; set ASMR_PYTHON to one."
    fi
fi
note "Using $("$python" -c 'import sys; print(sys.executable, sys.version.split()[0])')"

if ! command -v ffmpeg >/dev/null 2>&1; then
    warn "ffmpeg is not on PATH.  Decoding audio will fail until you install it
         (or point Settings -> ffmpeg at a binary).  See packaging/linux/README.md."
fi

# --- venv --------------------------------------------------------------------

extras=gui
if [ "$want_cuda" = 1 ]; then
    extras=gui,cuda
fi

mkdir -p -- "$prefix"
if [ -x "$venv/bin/python" ]; then
    note "Reusing the venv in $venv"
else
    note "Creating a venv in $venv"
    "$python" -m venv -- "$venv" \
        || die "python -m venv failed.  Debian and Ubuntu need the python3-venv package."
fi

note "Installing asmr-lrc[$extras] -- this pulls Qt, roughly 100 MB"
"$venv/bin/python" -m pip install --upgrade --quiet pip
"$venv/bin/python" -m pip install --upgrade "$repo_root[$extras]"

if [ ! -x "$launcher" ]; then
    die "the install finished but $launcher is missing."
fi

# --- post-install checks -----------------------------------------------------

if ! "$venv/bin/python" -c 'import sounddevice' >/dev/null 2>&1; then
    warn "sounddevice cannot load PortAudio.  Install libportaudio2 (see README.md);
         transcription still works, only the player needs it."
fi

xcb_plugin=$("$venv/bin/python" - <<'PY' 2>/dev/null || true
from pathlib import Path

import PySide6

plugin = Path(PySide6.__file__).parent / "Qt" / "plugins" / "platforms" / "libqxcb.so"
print(plugin if plugin.is_file() else "")
PY
)
if [ -n "$xcb_plugin" ] && command -v ldd >/dev/null 2>&1; then
    missing=$(ldd "$xcb_plugin" 2>/dev/null | awk '/not found/ { print $1 }' | sort -u | tr '\n' ' ')
    if [ -n "$missing" ]; then
        warn "the Qt xcb platform plugin cannot find: $missing
         The window will not open until those are installed; README.md lists the
         package names (usually libxcb-cursor0 or libxcb-cursor)."
    fi
fi

# --- desktop integration -----------------------------------------------------

if [ "$want_desktop" = 1 ]; then
    mkdir -p -- "$applications_dir" "$icons_dir" "$bin_dir"
    # Exec has to be absolute: a desktop launcher does not necessarily see
    # ~/.local/bin on PATH.
    awk -v launcher="$launcher" '{ gsub(/@LAUNCHER@/, launcher); print }' \
        "$script_dir/asmr-translation.desktop" >"$desktop_file.tmp"
    mv -- "$desktop_file.tmp" "$desktop_file"
    chmod 644 -- "$desktop_file"
    cp -- "$script_dir/asmr-translation.svg" "$icon_file"
    ln -sfn -- "$launcher" "$symlink"
    refresh_caches

    if command -v desktop-file-validate >/dev/null 2>&1; then
        desktop-file-validate "$desktop_file" \
            || warn "desktop-file-validate reported the above about $desktop_file."
    fi
    note "Installed $desktop_file"
fi

# --- next steps --------------------------------------------------------------

printf '\n'
note "Installed."
printf '\n'
echo "  Launch it       $launcher"
if [ "$want_desktop" = 1 ]; then
    echo "                  or 'asmr-translation' when ~/.local/bin is on PATH,"
    echo "                  or from the application menu"
fi
cat <<EOF

  Then, once (none of this is downloaded for you):
    1. fetch the faster-whisper large-v3 weights and point
       Settings -> ASR model at that directory
    2. ollama pull translategemma:4b
    3. ollama pull qwen3.5-9b-abliterated:latest

  Uninstall       $script_dir/install.sh --uninstall
EOF
