from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def log(event: str, **fields: Any) -> None:
    Path("logs").mkdir(exist_ok=True)
    payload = {"ts": datetime.now(UTC).isoformat(), "event": event, **fields}
    line = json.dumps(payload, ensure_ascii=False, default=str)
    print(line)
    logfile = Path("logs") / f"{datetime.now(UTC).date().isoformat()}.log"
    with logfile.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")

