"""Captions — platform-specific copy, not one caption pasted everywhere."""

from __future__ import annotations

from typing import Any


def variants(job_name: str, detail: str, cfg: dict[str, Any]) -> dict[str, str]:
    shop = cfg.get("shop_name", "Your Shop")
    tags = " ".join(cfg.get("hashtags", ["#plumbing", "#hvac", "#electrical",
                                       "#skilledtrades"]))
    area = cfg.get("service_area", "your area")
    return {
        "gbp": f"{detail or 'Another job done right'} — {shop}, serving {area}.",
        "facebook": (f"Before and after from this week's {job_name} job. "
                     f"{detail} Call {shop} when you need it done right."),
        "instagram": f"{detail or 'Before → after'} {tags}",
    }
