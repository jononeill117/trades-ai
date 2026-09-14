"""Selector-health checks for the social composer adapter."""

from __future__ import annotations


def checks(mode: str) -> list[dict]:
    return [{
        "name": "photo-marketer/composer:fixture",
        "html": "fixtures/social/composer.html",
        "selectors": ["#caption", "#post-submit"],
    }]
