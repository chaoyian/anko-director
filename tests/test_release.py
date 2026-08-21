from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_version.py"


def run_version_check(tag: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--tag", tag],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_release_version_is_consistent() -> None:
    result = run_version_check("v0.7.0")
    assert result.returncode == 0, result.stderr
    assert "version check passed: v0.7.0" in result.stdout


def test_release_version_rejects_wrong_tag() -> None:
    result = run_version_check("v9.9.9")
    assert result.returncode == 1
    assert "does not match project version v0.7.0" in result.stderr
