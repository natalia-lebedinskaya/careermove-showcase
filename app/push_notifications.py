from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Callable


UTC = timezone.utc


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def deliver_push_notifications(
    query: Callable[..., Any],
    execute: Callable[..., Any],
    user_id: int,
    jobs: list[dict[str, Any]],
    *,
    run_id: str,
    app_url: str,
) -> list[dict[str, str]]:
    """Send one Web Push digest per active device after a completed search."""
    golden = [job for job in jobs if int(job.get("score") or 0) >= 60]
    private_key = os.getenv("VAPID_PRIVATE_KEY", "").strip().replace("\\n", "\n")
    if not golden or not private_key:
        return []
    subscriptions = query(
        """
        SELECT endpoint,p256dh,auth FROM push_subscriptions
        WHERE user_id=? AND enabled=1 ORDER BY id
        """,
        (user_id,),
    )
    if subscriptions.empty:
        return []
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        return [{"channel": "push", "status": "error", "detail": "Web Push dependency is unavailable"}]

    url = f"{app_url.rstrip('/')}/?view=jobs&source=notification"
    payload = json.dumps({
        "title": f"CareerMove · найдено {len(golden)} подходящих",
        "body": "Новая автоматическая подборка готова. Нажмите, чтобы открыть вакансии.",
        "url": url,
        "tag": f"careermove-search-{run_id}",
    }, ensure_ascii=False)
    claims = {
        "sub": os.getenv(
            "VAPID_SUBJECT",
            "mailto:owner@example.com",
        ).strip(),
    }
    statuses: list[dict[str, str]] = []
    for row in subscriptions.to_dict("records"):
        endpoint = str(row.get("endpoint") or "")
        endpoint_hash = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:16]
        event_key = f"push:{user_id}:{run_id}:{endpoint_hash}"
        previous = query(
            "SELECT status FROM notification_events WHERE event_key=? AND user_id=?",
            (event_key, user_id),
        )
        if not previous.empty and str(previous.iloc[0]["status"]) == "sent":
            statuses.append({"channel": "push", "status": "already_sent", "detail": "Уже отправлено"})
            continue
        ok, detail = False, "Push notification failed"
        try:
            webpush(
                subscription_info={
                    "endpoint": endpoint,
                    "keys": {
                        "p256dh": str(row.get("p256dh") or ""),
                        "auth": str(row.get("auth") or ""),
                    },
                },
                data=payload,
                vapid_private_key=private_key,
                vapid_claims=claims,
                ttl=86400,
            )
            ok, detail = True, "Уведомление доставлено в push-сервис"
            execute(
                "UPDATE push_subscriptions SET last_used_at=? WHERE user_id=? AND endpoint=?",
                (_now_iso(), user_id, endpoint),
            )
        except WebPushException as error:
            status_code = getattr(getattr(error, "response", None), "status_code", 0)
            if status_code in {404, 410}:
                execute(
                    "UPDATE push_subscriptions SET enabled=0,last_used_at=? WHERE user_id=? AND endpoint=?",
                    (_now_iso(), user_id, endpoint),
                )
                detail = "Устаревшая подписка отключена"
            else:
                detail = f"Push временно недоступен ({status_code or 'network'})"
        if previous.empty:
            execute(
                """
                INSERT INTO notification_events(event_key,user_id,channel,status,detail,created_at)
                VALUES(?,?,?,?,?,?)
                """,
                (event_key, user_id, "push", "sent" if ok else "error", detail, _now_iso()),
            )
        else:
            execute(
                """
                UPDATE notification_events SET status=?,detail=?,created_at=?
                WHERE event_key=? AND user_id=?
                """,
                ("sent" if ok else "error", detail, _now_iso(), event_key, user_id),
            )
        statuses.append({"channel": "push", "status": "sent" if ok else "error", "detail": detail})
    return statuses
