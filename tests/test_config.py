import os
from pathlib import Path

from sales_automation.config import expand_env, load_config


def test_expand_env(monkeypatch):
    monkeypatch.setenv("DB_HOST", "localhost")
    assert expand_env({"host": "${DB_HOST}"}) == {"host": "localhost"}


def test_load_config_with_minimal_yaml(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DB_HOST", "localhost")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
database:
  host: ${DB_HOST}
  port: 5432
sender:
  dry_run: true
sequence:
  - step: 1
    delay_days: 0
""",
        encoding="utf-8",
    )
    app = load_config(cfg)
    assert app.database["host"] == "localhost"
    assert app.sender["dry_run"] is True
    assert app.sequence[0]["step"] == 1

