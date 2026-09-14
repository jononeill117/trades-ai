"""Selector-health checks for the FSM jobs-list adapter."""

from __future__ import annotations


def checks(mode: str) -> list[dict]:
    return [{
        "name": "meeting-prep/jobs-list:fixture",
        "html": "fixtures/fsm_jobs/jobs_page.html",
        "patterns": {"job-row": r'<tr class="job-row"',
                     "job-cell": r'<td class="j-[a-z_]+"'},
    }]
