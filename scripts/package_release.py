from __future__ import annotations

import argparse
import hashlib
import shutil
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", required=True)
    args = parser.parse_args()
    source = Path("dist/anko-director")
    if not source.is_dir():
        raise SystemExit("dist/anko-director does not exist")
    release_dir = Path("release")
    release_dir.mkdir(exist_ok=True)
    base = release_dir / f"anko-director-{args.platform}"
    archive = Path(shutil.make_archive(str(base), "zip", source.parent, source.name))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(archive.suffix + ".sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
