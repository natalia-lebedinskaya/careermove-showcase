from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import datetime, timezone

from api.main import db, run_due_scheduled_searches, run_search_task


def run_one(user_id: int, run_id: str, use_ai: bool = False) -> dict[str, int]:
    frame = db.query(
        """
        SELECT run_id,user_id,status FROM search_runs_v2
        WHERE run_id=? AND user_id=?
        """,
        (run_id, user_id),
    )
    if frame.empty:
        raise RuntimeError("Requested search run does not exist for this user.")
    if str(frame.iloc[0]["status"]) not in {"queued", "running"}:
        return {"users": 1, "completed": 0}
    run_search_task(run_id, user_id, use_ai)
    return {"users": 1, "completed": 1}


def run_all_users(use_ai: bool = False) -> dict[str, int]:
    if os.getenv("CAREERMOVE_SCHEMA_ON_START", "1").strip().lower() not in {"0", "false", "no"}:
        db.ensure_schema()
    return run_due_scheduled_searches(use_ai=use_ai)


def main() -> None:
    parser = argparse.ArgumentParser(description="CareerMove scheduled vacancy search")
    parser.add_argument("--use-ai", action="store_true", help="Enable optional model reranking")
    parser.add_argument("--user-id", type=int, help="Run a queued search for one user")
    parser.add_argument("--run-id", help="Queued search run id")
    args = parser.parse_args()
    if bool(args.user_id) != bool(args.run_id):
        parser.error("--user-id and --run-id must be provided together")
    result = (
        run_one(args.user_id, args.run_id, use_ai=args.use_ai)
        if args.user_id and args.run_id
        else run_all_users(use_ai=args.use_ai)
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
