from __future__ import annotations

import json
import re
from pathlib import Path

__version__ = "1.0.6"

_PLUGIN_DIR = Path(__file__).resolve().parent
_CUSTOM_NODES_DIR = _PLUGIN_DIR.parent

_VERSION_RE = re.compile(r'__version__\s*=\s*["\']([^"\']+)["\']')
_PYPROJECT_VERSION_RE = re.compile(r'^\s*version\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)


def _version_from_init_py(init_path: Path) -> str | None:
    """Try to read __version__ from a package __init__.py without importing."""
    if not init_path.is_file():
        return None
    try:
        text = init_path.read_text(encoding="utf-8", errors="replace")
        m = _VERSION_RE.search(text)
        return m.group(1).strip() if m else None
    except Exception:
        return None


def _version_from_metadata(package_name: str) -> str | None:
    """Try to get version from importlib.metadata (pip-installed packages)."""
    try:
        from importlib.metadata import version
        for name in (package_name, package_name.replace("_", "-"), package_name.replace("-", "_")):
            try:
                return version(name)
            except Exception:
                continue
    except Exception:
        pass
    return None


def _version_from_pyproject_toml(package_dir: Path) -> str | None:
    """Try to read version from pyproject.toml ([project] or [tool.poetry] version)."""
    path = package_dir / "pyproject.toml"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in _PYPROJECT_VERSION_RE.finditer(text):
            return m.group(1).strip()
    except Exception:
        pass
    return None


def _version_from_package_json(package_dir: Path) -> str | None:
    """Try to read version from package.json."""
    path = package_dir / "package.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        if isinstance(data.get("version"), str) and data["version"].strip():
            return data["version"].strip()
    except Exception:
        pass
    return None


def _version_from_version_file(package_dir: Path) -> str | None:
    """Try __version__ in version.py (or similar) in the package dir."""
    for name in ("version.py", "version.json"):
        path = package_dir / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            if name.endswith(".json"):
                data = json.loads(text)
                if isinstance(data.get("version"), str) and data["version"].strip():
                    return data["version"].strip()
            else:
                m = _VERSION_RE.search(text)
                if m:
                    return m.group(1).strip()
        except Exception:
            pass
    return None


def _is_git_repo(package_dir: Path) -> bool:
    """True if package_dir is the root of a git repo (has .git)."""
    return (package_dir / ".git").exists()


def get_installed_modules() -> list[dict]:
    """
    List installed custom node modules (directories under custom_nodes with __init__.py)
    and their version if discoverable.

    Returns a list of {"name": str, "version": str | null} sorted by name.
    Version is resolved from: __init__.py, importlib.metadata, pyproject.toml,
    package.json, version.py; if still unknown and the dir is a git repo, "nightly".
    Does not import custom node code.
    """
    result: list[dict] = []
    if not _CUSTOM_NODES_DIR.is_dir():
        return result

    for path in sorted(_CUSTOM_NODES_DIR.iterdir()):
        if not path.is_dir():
            continue
        init_py = path / "__init__.py"
        if not init_py.is_file():
            continue
        name = path.name
        version = (
            _version_from_init_py(init_py)
            or _version_from_metadata(name)
            or _version_from_pyproject_toml(path)
            or _version_from_package_json(path)
            or _version_from_version_file(path)
        )
        if version is None and _is_git_repo(path):
            version = "nightly"
        result.append({"name": name, "version": version})

    return result


def get_version_info_payload() -> dict:
    """
    Build the JSON payload for GET /workflowui/version_info.

    Includes:
    - workflowui_plugin_version: this plugin's version
    - installed_custom_nodes: list of { name, version } for each custom node package
    """
    return {
        "workflowui_plugin_version": __version__,
        "installed_custom_nodes": get_installed_modules(),
    }
