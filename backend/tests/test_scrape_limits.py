import pytest

import backend.services.scrape_limits as scrape_limits_module
from backend.services.scrape_limits import (
    get_scrape_limits,
    platform_limit_payload,
    summarize_requested_items,
    validate_platform_config_limits,
)


def test_default_scrape_limits_are_open_source_defaults():
    limits = get_scrape_limits({})

    assert limits.plan_id == "open_source"
    assert limits.plan_label == "Open Source"
    assert limits.max_platforms_per_search == 8
    assert limits.max_items_per_platform == 25000
    assert limits.max_items_per_search == 25000


def test_platform_limit_payload_exposes_monthly_remaining_without_usage():
    payload = platform_limit_payload({})

    assert payload["monthly_item_allowance"] == 25000
    assert payload["monthly_items_used"] == 0
    assert payload["monthly_items_remaining"] == 25000
    assert payload["available_plans"] == []


def test_limits_do_not_depend_on_private_user_emails():
    limits = get_scrape_limits({"email": "owner@example.com"})

    assert limits.plan_id == "open_source"
    assert limits.max_items_per_search == 25000
    assert limits.max_creators == 250


def test_requested_items_summary_counts_enabled_sources_only():
    enabled_count, total_items = summarize_requested_items({
        "youtube": {"enabled": True, "url": "https://youtube.com/@a", "maxItems": 10},
        "instagram": {"enabled": True, "url": "https://instagram.com/a", "maxItems": 7},
        "tiktok": {"enabled": False, "url": "https://tiktok.com/@a", "maxItems": 50},
        "twitter": {"enabled": True, "url": "", "maxItems": 5},
    })

    assert enabled_count == 2
    assert total_items == 17


def test_open_source_limits_allow_reasonable_budget_split_across_sources():
    limits = get_scrape_limits({})

    validate_platform_config_limits({
        "youtube": {"enabled": True, "url": "https://youtube.com/@a", "maxItems": 400},
        "instagram": {"enabled": True, "url": "https://instagram.com/a", "maxItems": 200},
    }, limits)


def test_open_source_limits_reject_too_many_sources(monkeypatch):
    monkeypatch.setattr(scrape_limits_module.settings, "SCRAPE_MAX_PLATFORMS_PER_SEARCH", 2)
    limits = get_scrape_limits({})

    with pytest.raises(ValueError, match="allows 2 sources"):
        validate_platform_config_limits({
            "youtube": {"enabled": True, "url": "https://youtube.com/@a", "maxItems": 5},
            "instagram": {"enabled": True, "url": "https://instagram.com/a", "maxItems": 5},
            "tiktok": {"enabled": True, "url": "https://tiktok.com/@a", "maxItems": 5},
        }, limits)


def test_open_source_limits_reject_more_than_search_budget(monkeypatch):
    monkeypatch.setattr(scrape_limits_module.settings, "SCRAPE_MAX_ITEMS_PER_SEARCH", 100)
    limits = get_scrape_limits({})

    with pytest.raises(ValueError, match="total items"):
        validate_platform_config_limits({
            "youtube": {"enabled": True, "url": "https://youtube.com/@a", "maxItems": 60},
            "instagram": {"enabled": True, "url": "https://instagram.com/a", "maxItems": 41},
        }, limits)
