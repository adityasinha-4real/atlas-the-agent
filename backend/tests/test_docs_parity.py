"""Doc-lint as a test: versions and config stay consistent across the repo (M6 §17-18).

These checks make the "0.5.0 in three files but 0.6.0 in a fourth" and the
"undocumented ATLAS_* setting" classes of error impossible — they fail CI rather
than shipping stale docs. They read the real files, so they can never drift.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from atlas import __version__
from atlas.core.config import Settings

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _env_example_keys() -> set[str]:
    text = (_REPO_ROOT / "backend" / ".env.example").read_text(encoding="utf-8")
    keys: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip())
    return keys


def test_version_is_consistent_across_the_repo() -> None:
    """backend __version__ == pyproject == frontend package.json (one release)."""
    pyproject = tomllib.loads(
        (_REPO_ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    )
    package = json.loads(
        (_REPO_ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
    )
    assert __version__ == pyproject["project"]["version"] == package["version"]


def test_version_is_the_m7_release() -> None:
    assert __version__ == "0.7.0"


def test_every_setting_is_documented_in_env_example() -> None:
    """Each ATLAS_* setting appears in .env.example — no phantom or undocumented knobs."""
    documented = _env_example_keys()
    fields = set(Settings.model_fields)
    expected = {f"ATLAS_{name.upper()}" for name in fields}
    missing = expected - documented
    assert not missing, f".env.example is missing: {sorted(missing)}"


def test_env_example_has_no_unknown_variables() -> None:
    """Every ATLAS_* var in .env.example maps to a real Settings field (no dead vars)."""
    known = {f"ATLAS_{name.upper()}" for name in Settings.model_fields}
    unknown = {k for k in _env_example_keys() if k.startswith("ATLAS_")} - known
    assert not unknown, f".env.example documents non-existent settings: {sorted(unknown)}"


def test_changelog_documents_the_current_version() -> None:
    changelog = (_REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(rf"^## \[{re.escape(__version__)}\]", changelog, re.MULTILINE), (
        f"CHANGELOG.md has no [{__version__}] section"
    )
