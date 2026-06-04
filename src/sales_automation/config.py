from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


@dataclass(frozen=True)
class AppConfig:
    raw: dict[str, Any]
    root_dir: Path

    @property
    def database(self) -> dict[str, Any]:
        return self.raw["database"]

    @property
    def apis(self) -> dict[str, Any]:
        return self.raw.get("apis", {})

    @property
    def sender(self) -> dict[str, Any]:
        return self.raw.get("sender", {})

    @property
    def sequence(self) -> list[dict[str, Any]]:
        return self.raw.get("sequence", [])


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip().lstrip("\ufeff"), value.strip().strip('"').strip("'"))


def expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, list):
        return [expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: expand_env(item) for key, item in value.items()}
    return value


def load_config(path: str | Path) -> AppConfig:
    load_dotenv()
    config_path = Path(path)
    text = config_path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        raw = yaml.safe_load(text) or {}
    except ModuleNotFoundError:
        raw = _minimal_yaml(text)
    return AppConfig(raw=expand_env(raw), root_dir=config_path.parent.resolve())


def _minimal_yaml(text: str) -> dict[str, Any]:
    """Small YAML subset parser for config.example.yaml when PyYAML is absent."""
    result: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, result)]
    for original in text.splitlines():
        if not original.strip() or original.lstrip().startswith("#"):
            continue
        indent = len(original) - len(original.lstrip(" "))
        line = original.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if line.startswith("- "):
            item: dict[str, Any] = {}
            parent.append(item)
            rest = line[2:]
            if rest and ":" in rest:
                key, value = rest.split(":", 1)
                item[key.strip()] = _coerce(value.strip())
            stack.append((indent, item))
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value == "":
            next_is_list = _next_content_starts_with_dash(text, original)
            container: Any = [] if next_is_list else {}
            parent[key] = container
            stack.append((indent, container))
        else:
            parent[key] = _coerce(value)
    return result


def _next_content_starts_with_dash(text: str, current: str) -> bool:
    lines = text.splitlines()
    idx = lines.index(current)
    for line in lines[idx + 1 :]:
        if line.strip() and not line.lstrip().startswith("#"):
            return line.strip().startswith("- ")
    return False


def _coerce(value: str) -> Any:
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if value.isdigit():
        return int(value)
    return value.strip('"').strip("'")
