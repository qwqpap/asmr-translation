"""The Linux packaging assets are only exercised on Linux, so guard them here.

Nothing in the test suite installs the desktop entry, and a Windows developer
never runs ``install.sh``.  The invariants that would silently break a Linux
install -- CRLF line endings, a drifting Exec placeholder, an Icon name that no
longer matches the shipped file, a Python gate that disagrees with
``requires-python`` -- are cheap to assert and expensive to discover.
"""

from __future__ import annotations

import configparser
import re
import shutil
import subprocess
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
LINUX = ROOT / "packaging" / "linux"
DESKTOP_FILE = LINUX / "asmr-translation.desktop"
INSTALL_SCRIPT = LINUX / "install.sh"
ICON_FILE = LINUX / "asmr-translation.svg"

_HEREDOC = re.compile(r"<<-?'?(?P<tag>[A-Za-z_][A-Za-z0-9_]*)'?")


def desktop_entry() -> configparser.SectionProxy:
    parser = configparser.ConfigParser(interpolation=None, comment_prefixes=("#",))
    parser.optionxform = str  # Desktop keys are case-sensitive: Name != name.
    parser.read_string(DESKTOP_FILE.read_text(encoding="utf-8"))
    return parser["Desktop Entry"]


def install_script() -> str:
    return INSTALL_SCRIPT.read_text(encoding="utf-8")


def command_lines(script: str) -> list[str]:
    """Lines the shell actually executes: no comments, no heredoc prose.

    The help text and the closing summary mention ``ollama pull`` and sudo on
    purpose, so a plain substring search cannot tell documentation from an
    invocation.
    """
    lines: list[str] = []
    terminator: str | None = None
    for raw in script.splitlines():
        stripped = raw.strip()
        if terminator is not None:
            if stripped == terminator:
                terminator = None
            continue
        match = _HEREDOC.search(raw)
        if match is not None:
            terminator = match["tag"]
        if stripped and not stripped.startswith("#"):
            lines.append(stripped)
    return lines


def test_desktop_entry_has_the_required_keys() -> None:
    entry = desktop_entry()
    assert entry["Type"] == "Application"
    assert entry["Name"] == "ASMR Translation"
    assert entry["Terminal"] == "false"
    # Trailing semicolon: Categories is a list, and some parsers need the sentinel.
    assert entry["Categories"].endswith(";")
    assert "AudioVideo" in entry["Categories"].split(";")


def test_exec_is_a_placeholder_the_installer_fills_in() -> None:
    # Relying on ~/.local/bin being on the session PATH is what breaks launchers,
    # so the installer writes an absolute path instead.
    assert desktop_entry()["Exec"] == "@LAUNCHER@"
    assert "@LAUNCHER@" in install_script()


def test_exec_takes_no_field_codes() -> None:
    # asmr_gui.app ignores extra argv entries, so %F would silently drop the file
    # the user dragged onto the launcher.
    exec_line = desktop_entry()["Exec"]
    assert "%" not in exec_line


def test_icon_name_matches_the_shipped_file() -> None:
    assert desktop_entry()["Icon"] == ICON_FILE.stem
    assert f"{ICON_FILE.name}" in install_script()


def test_startup_wm_class_matches_the_qt_desktop_file_name() -> None:
    # Qt derives WM_CLASS from setDesktopFileName(); a mismatch makes the taskbar
    # show a second, iconless entry for the running window.
    app_source = (ROOT / "asmr_gui" / "app.py").read_text(encoding="utf-8")
    slug = desktop_entry()["StartupWMClass"]
    assert f'setDesktopFileName("{slug}")' in app_source
    assert DESKTOP_FILE.stem == slug


def test_icon_is_a_scalable_svg() -> None:
    root = ElementTree.fromstring(ICON_FILE.read_text(encoding="utf-8"))
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    # Without viewBox the icon cannot be rendered at the sizes the theme asks for.
    assert root.get("viewBox")


def test_installer_uses_unix_line_endings() -> None:
    # A CR in the shebang makes the kernel look for an interpreter named "bash\r".
    assert b"\r" not in INSTALL_SCRIPT.read_bytes()
    assert b"\r" not in DESKTOP_FILE.read_bytes()


def test_installer_fails_loudly_and_never_escalates() -> None:
    script = install_script()
    assert script.startswith("#!/usr/bin/env bash\n")
    assert "set -euo pipefail" in script
    assert 'if [ "$(id -u)" = 0 ]; then' in script
    # A per-user install has no reason to touch anything outside $HOME.
    for line in command_lines(script):
        assert "sudo" not in line.split(), line


def test_installer_only_downloads_through_pip() -> None:
    script = install_script()
    for line in command_lines(script):
        assert line.split()[0] not in {"curl", "wget", "ollama"}, line
    assert '"$venv/bin/python" -m pip install --upgrade "$repo_root[$extras]"' in script


def test_installer_python_gate_matches_requires_python() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.12,<3.14"' in pyproject
    assert "(3, 12) <= sys.version_info < (3, 14)" in install_script()


def test_installer_installs_the_gui_extra_under_xdg_data_home() -> None:
    script = install_script()
    assert "extras=gui" in script
    assert "extras=gui,cuda" in script
    assert "data_home=${XDG_DATA_HOME:-$HOME/.local/share}" in script


def test_uninstall_keeps_user_data() -> None:
    script = install_script()
    assert "--uninstall" in script
    for variable in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        assert variable in script


def test_installer_parses_as_bash() -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not available")
    subprocess.run([bash, "-n", str(INSTALL_SCRIPT)], check=True)


def test_installer_is_committed_executable() -> None:
    git = shutil.which("git")
    if git is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    result = subprocess.run(
        [git, "ls-files", "-s", "--", "packaging/linux/install.sh"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        pytest.skip("install.sh is not tracked yet")
    assert result.stdout.split()[0] == "100755"


def test_linux_readme_documents_the_system_packages() -> None:
    readme = (LINUX / "README.md").read_text(encoding="utf-8")
    # The three dependencies the wheels cannot provide, plus the IME the editor
    # needs; every one of these has cost a working install before.
    for token in ("ffmpeg", "libportaudio2", "libxcb-cursor0", "QT_IM_MODULE"):
        assert token in readme
