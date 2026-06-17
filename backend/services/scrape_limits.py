"""Centralized scrape limits for the open-source build."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Tuple

from backend.db import db
from backend.settings import settings


@dataclass(frozen=True)
class ScrapeLimits:
    plan_id: str
    plan_label: str
    max_platforms_per_search: int
    max_items_per_platform: int
    max_items_per_search: int
    max_creators: int
    monthly_item_allowance: int

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def get_scrape_limits(user: Dict[str, Any] | None = None) -> ScrapeLimits:
    return ScrapeLimits(
        plan_id="open_source",
        plan_label="Open Source",
        max_platforms_per_search=_positive_int(settings.SCRAPE_MAX_PLATFORMS_PER_SEARCH, 8),
        max_items_per_platform=_positive_int(settings.SCRAPE_MAX_ITEMS_PER_PLATFORM, 25000),
        max_items_per_search=_positive_int(settings.SCRAPE_MAX_ITEMS_PER_SEARCH, 25000),
        max_creators=_positive_int(settings.SCRAPE_MAX_CREATORS, 250),
        monthly_item_allowance=_positive_int(settings.SCRAPE_MONTHLY_ITEM_ALLOWANCE, 25000),
    )


def summarize_requested_items(platform_configs: Dict[str, Any]) -> Tuple[int, int]:
    enabled = [
        cfg for cfg in (platform_configs or {}).values()
        if isinstance(cfg, dict) and cfg.get("enabled") and cfg.get("url")
    ]
    total_items = 0
    for cfg in enabled:
        try:
            total_items += max(1, int(cfg.get("maxItems") or 1))
        except (TypeError, ValueError):
            total_items += 1
    return len(enabled), total_items


def validate_platform_config_limits(platform_configs: Dict[str, Any], limits: ScrapeLimits) -> None:
    """Raise ValueError if a config exceeds the supplied open-source limits."""
    enabled_count, total_items = summarize_requested_items(platform_configs)
    if enabled_count > limits.max_platforms_per_search:
        raise ValueError(
            f"{limits.plan_label} allows {limits.max_platforms_per_search} source"
            f"{'' if limits.max_platforms_per_search == 1 else 's'} per search."
        )
    if total_items > limits.max_items_per_search:
        raise ValueError(
            f"{limits.plan_label} allows {limits.max_items_per_search} total items per search."
        )
    for key, cfg in (platform_configs or {}).items():
        if not isinstance(cfg, dict) or not cfg.get("enabled") or not cfg.get("url"):
            continue
        try:
            requested = max(1, int(cfg.get("maxItems") or 1))
        except (TypeError, ValueError):
            requested = 1
        if requested > limits.max_items_per_platform:
            raise ValueError(
                f"{limits.plan_label} allows {limits.max_items_per_platform} items per source. "
                f"Reduce {key} before searching."
            )


def platform_limit_payload(user: Dict[str, Any] | None = None) -> Dict[str, Any]:
    user = user or {}
    limits = get_scrape_limits(user)
    usage = {}
    if user.get("id"):
        usage = get_monthly_usage(int(user["id"]))
    used = int(usage.get("scrape_items_requested") or 0)
    remaining = max(0, limits.monthly_item_allowance - used)
    return {
        **limits.as_dict(),
        "monthly_usage": usage,
        "monthly_items_used": used,
        "monthly_items_remaining": remaining,
        "available_plans": [],
    }


def _now_period_start() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).date().isoformat()


def get_monthly_usage(user_id: int) -> Dict[str, Any]:
    period_start = _now_period_start()
    row = db.execute_one(
        """
        SELECT period_start, scrape_searches, scrape_items_requested, scrape_items_found
        FROM user_usage_monthly
        WHERE user_id = %s AND period_start = %s
        """,
        (user_id, period_start),
    ) or {}
    return {
        "period_start": str(row.get("period_start") or period_start),
        "scrape_searches": int(row.get("scrape_searches") or 0),
        "scrape_items_requested": int(row.get("scrape_items_requested") or 0),
        "scrape_items_found": int(row.get("scrape_items_found") or 0),
    }


def reserve_scrape_usage(user_id: int, requested_items: int, user: Dict[str, Any] | None = None) -> Dict[str, Any]:
    requested_items = max(0, int(requested_items or 0))
    limits = get_scrape_limits(user)
    usage = get_monthly_usage(user_id)
    projected = usage["scrape_items_requested"] + requested_items
    if projected > limits.monthly_item_allowance:
        raise ValueError(
            f"{limits.plan_label} allows {limits.monthly_item_allowance} scrape items per month. "
            "Reduce the search size or raise the self-hosted scrape limit."
        )
    period_start = usage["period_start"]
    db.execute_update(
        """
        INSERT INTO user_usage_monthly (user_id, period_start, scrape_searches, scrape_items_requested)
        VALUES (%s, %s, 1, %s)
        ON CONFLICT (user_id, period_start) DO UPDATE
        SET scrape_searches = user_usage_monthly.scrape_searches + 1,
            scrape_items_requested = user_usage_monthly.scrape_items_requested + EXCLUDED.scrape_items_requested,
            updated_at = NOW()
        """,
        (user_id, period_start, requested_items),
    )
    return get_monthly_usage(user_id)


def add_found_scrape_items(user_id: int, found_items: int) -> Dict[str, Any]:
    found_items = max(0, int(found_items or 0))
    period_start = _now_period_start()
    db.execute_update(
        """
        INSERT INTO user_usage_monthly (user_id, period_start, scrape_items_found)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, period_start) DO UPDATE
        SET scrape_items_found = user_usage_monthly.scrape_items_found + EXCLUDED.scrape_items_found,
            updated_at = NOW()
        """,
        (user_id, period_start, found_items),
    )
    return get_monthly_usage(user_id)
