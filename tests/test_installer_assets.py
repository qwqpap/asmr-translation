from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_installer_manifest_has_pinned_artifact_shape() -> None:
    manifest = json.loads(
        (ROOT / "installer" / "manifest" / "artifacts.json").read_text(encoding="utf-8")
    )
    assert manifest["schema_version"] == 1
    assert manifest["release"] == "0.3.0"
    assert manifest["python"]["url"].startswith("https://")
    assert manifest["pip_bootstrap"]["url"].startswith("https://")
    assert manifest["wheels"]["project"]["path"].startswith("../wheel/")
    assert manifest["wheels"]["cpu_lock"]["path"].endswith("requirements-cpu.lock")
    assert manifest["wheels"]["cuda_lock"]["path"].endswith("requirements-cuda.lock")


def test_bootstrap_script_keeps_downloads_atomic_and_hash_checked() -> None:
    script = (ROOT / "installer" / "bootstrap" / "bootstrap.ps1").read_text(encoding="utf-8")
    assert "SHA-256 verification failed" in script
    assert ".part" in script
    assert "Headers.Range" in script
    assert "Safe-ExtractZip" in script
    assert 'Write-Event "download_progress"' in script


def test_installer_build_requires_release_hashes() -> None:
    script = (ROOT / "installer" / "build.ps1").read_text(encoding="utf-8")
    assert "REPLACE_" in script
    assert "Assert-Hash" in script
    assert "ProductVersion" in script
