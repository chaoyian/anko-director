from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def match_version(path: str, pattern: str, label: str) -> str:
    match = re.search(pattern, read(path))
    if not match:
        raise ValueError(f"Could not find {label} version in {path}")
    return match.group(1)


def collected_versions() -> dict[str, str]:
    project = tomllib.loads(read("pyproject.toml"))
    versions = {
        "pyproject": project["project"]["version"],
        "package": match_version(
            "src/anko_director/__init__.py", r'__version__\s*=\s*"([^"]+)"', "package"
        ),
        "FastAPI": match_version(
            "src/anko_director/web/app.py",
            r'FastAPI\([^\n]+version="([^"]+)"',
            "FastAPI",
        ),
    }
    template_dir = ROOT / "src" / "anko_director" / "web" / "templates"
    for template in sorted(template_dir.glob("*.html")):
        path = template.relative_to(ROOT).as_posix()
        for index, version in enumerate(
            re.findall(r"[?&]v=([0-9]+\.[0-9]+\.[0-9]+)", read(path)), start=1
        ):
            versions[f"{path} asset {index}"] = version
    return versions


def check(tag: str | None = None) -> str:
    versions = collected_versions()
    expected = versions["pyproject"]
    mismatches = {label: value for label, value in versions.items() if value != expected}
    if mismatches:
        details = ", ".join(f"{label}={value}" for label, value in mismatches.items())
        raise ValueError(f"Version mismatch: expected {expected}; {details}")
    if tag and tag != f"v{expected}":
        raise ValueError(f"Tag {tag!r} does not match project version v{expected}")
    for path in ("README.md", "README.en.md"):
        if f"v{expected}" not in read(path):
            raise ValueError(f"{path} does not mention v{expected}")
    return expected


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify release version consistency")
    parser.add_argument("--tag")
    args = parser.parse_args()
    try:
        version = check(args.tag)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        print(f"version check failed: {exc}", file=sys.stderr)
        return 1
    print(f"version check passed: v{version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
