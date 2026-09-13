"""Config loading: .env files, environment variables, YAML.

No external config framework on purpose — a shop owner should be able to open
these files and understand every line. YAML is the one real dependency
(PyYAML) because availability rules and selector maps are much easier to edit
in YAML than in JSON.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


def repo_root() -> Path:
    """The repository root (the directory that contains this file's parent)."""
    return Path(__file__).resolve().parent.parent


def load_dotenv(path: Path | str | None = None) -> None:
    """Minimal .env loader: KEY=VALUE lines, # comments, no interpolation.

    Existing environment variables always win over the file, so real env vars
    are never clobbered by a stale .env.
    """
    path = Path(path) if path else repo_root() / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value else default


def require_env(name: str) -> str:
    value = env(name)
    if not value:
        raise RuntimeError(
            f"{name} is not set. Copy .env.example to .env and fill it in, "
            f"or export {name}=... — see the README's setup section."
        )
    return value


def load_yaml(path: Path | str) -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text())
    return data or {}
