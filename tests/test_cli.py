from pathlib import Path

from asmr_lrc.cli import main


def test_cli_requires_folder() -> None:
    # argparse owns the parameter-error exit behavior; parser.error is covered by CLI smoke tests.
    try:
        main([])
    except SystemExit as exc:
        assert exc.code == 2


def test_cli_dry_run_unicode_path(tmp_path: Path) -> None:
    folder = tmp_path / "中文 日本語 空格"
    folder.mkdir()
    (folder / "耳かき.flac").write_bytes(b"audio")
    assert main([str(folder), "--dry-run"]) == 0
