from __future__ import annotations

import argparse
import base64
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sales_automation.config import load_config
from sales_automation.db import Database, Repository
from sales_automation.importers import parse_company_seed_upload
from sales_automation.linkedin_public_search import LinkedInPublicSearchService
from sales_automation.quotas import QuotaService


def _write_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _resolve_user(repo: Repository, username: str) -> dict[str, Any]:
    user = next(
        (
            item
            for item in repo.list_users()
            if str(item.get("username") or "").casefold() == username.casefold()
        ),
        None,
    )
    if not user or not user.get("active", True):
        raise RuntimeError(f"Active user not found: {username}")
    return user


def main() -> None:
    parser = argparse.ArgumentParser(description="Source contacts from a company seed CSV/XLSX without sending email.")
    parser.add_argument("input_file")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--per-company-limit", type=int, default=3)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    input_path = Path(args.input_file).resolve()
    output_path = Path(args.output).resolve()
    per_company_limit = max(1, min(int(args.per_company_limit), 10))
    workers = max(1, min(int(args.workers), 4))
    config = load_config(args.config)
    repo = Repository(Database(config))
    user = _resolve_user(repo, args.username)
    seeds = parse_company_seed_upload(
        filename=input_path.name,
        content_base64=base64.b64encode(input_path.read_bytes()).decode("ascii"),
        default_location="Malaysia",
    )
    if not seeds:
        raise RuntimeError("No company seeds were parsed from the input file")

    requested = len(seeds) * per_company_limit
    quota = QuotaService(config, repo)
    snapshot = quota.snapshot(user)
    remaining = min(
        int(snapshot["source"]["remaining_user"]),
        int(snapshot["source"]["remaining_global"]),
    )
    if requested > remaining:
        raise RuntimeError(f"Source quota insufficient: need {requested}, remaining {remaining}")

    payload: dict[str, Any] = {
        "source_file": str(input_path),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "username": user["username"],
        "per_company_limit": per_company_limit,
        "parsed_companies": len(seeds),
        "completed_companies": 0,
        "seeds": seeds,
        "tasks": [],
        "errors": [],
    }
    _write_checkpoint(output_path, payload)

    def source_seed(seed: dict[str, Any]) -> dict[str, Any]:
        return LinkedInPublicSearchService(config, repo).run_company_seeds(
            [seed],
            per_company_limit=per_company_limit,
            user=user,
            auto_queue=False,
        )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(source_seed, seed): seed for seed in seeds}
        for index, future in enumerate(as_completed(futures), start=1):
            seed = futures[future]
            try:
                batch = future.result()
                payload["tasks"].extend(batch.get("tasks") or [])
                used = int(batch.get("results") or 0)
                if used:
                    quota.consume(user, "source", used)
            except Exception as exc:
                payload["errors"].append(
                    {
                        "company_name": seed.get("company_name") or "",
                        "error": str(exc)[:1000],
                    }
                )
            payload["completed_companies"] = index
            _write_checkpoint(output_path, payload)
            print(
                json.dumps(
                    {
                        "progress": f"{index}/{len(seeds)}",
                        "company": seed.get("company_name") or "",
                        "tasks": len(payload["tasks"]),
                        "errors": len(payload["errors"]),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    task_ids = [
        int(item["task_id"])
        for item in payload["tasks"]
        if item.get("task_id")
    ]
    payload["batch_report"] = repo.company_seed_batch_report(task_ids, user=user)
    payload["completed_at"] = datetime.now(timezone.utc).isoformat()
    payload["quota"] = quota.snapshot(user)
    _write_checkpoint(output_path, payload)


if __name__ == "__main__":
    main()
