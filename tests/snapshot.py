"""Minimal JSON snapshot testing.

``assert_snapshot(name, value)`` compares ``value`` to tests/snapshots/<name>.json. The file
is written when it's missing or when SNAPSHOT_UPDATE=1 is set. Review and commit it like
code: a snapshot diff is a change to what players see.
"""

import json
import os
from pathlib import Path

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"


def assert_snapshot(name: str, value: object) -> None:
    path = SNAPSHOT_DIR / f"{name}.json"
    rendered = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if os.environ.get("SNAPSHOT_UPDATE") == "1" or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
        return
    expected = path.read_text(encoding="utf-8")
    assert rendered == expected, (
        f"Snapshot {name!r} changed. If intended, rerun with SNAPSHOT_UPDATE=1 and commit "
        f"{path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path}."
    )
