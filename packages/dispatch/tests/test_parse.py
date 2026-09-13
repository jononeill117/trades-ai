"""Parser tests — run the real worker on the bundled fixtures."""

import json
import subprocess
import sys
from pathlib import Path

from packages.dispatch.parse_worker import parse_work_order

FIXTURES = Path(__file__).parents[3] / "fixtures" / "work_orders"


def test_parses_labeled_work_order():
    result = parse_work_order((FIXTURES / "wo-4471.eml").read_text())
    assert result["source_id"] == "WO-4471"
    assert result["customer_name"] == "Maple Property Group"
    assert result["site_address"].startswith("12 Birch Ln")
    assert result["trade"] == "plumbing"
    assert result["priority"] == "high"
    assert result["sla_start"] == "2026-09-14 08:00"
    assert result["sla_end"] == "2026-09-16 17:00"
    assert result["tenant_name"] == "Rosa Delgado"
    assert result["tenant_phone"] == "937-555-0184"
    assert result["tenant_email"] == "rosa.delgado@example.com"
    assert not result["needs_review"]


def test_infers_trade_and_priority_from_notes():
    # wo-4472 has no Trade/Priority labels — "furnace"/"no heat" -> hvac,
    # "urgent"/"no heat" -> emergency.
    result = parse_work_order((FIXTURES / "wo-4472.eml").read_text())
    assert result["source_id"] == "WO-4472"
    assert result["trade"] == "hvac"
    assert result["priority"] == "emergency"
    assert result["tenant_phone"] == "937-555-0123"


def test_worker_script_runs_standalone():
    # The same file the sandbox runs — verify the marker protocol end to end.
    out = subprocess.run(
        [sys.executable, str(Path(__file__).parents[1] / "parse_worker.py"),
         str(FIXTURES / "wo-4471.eml")],
        capture_output=True, text=True, check=True,
    )
    marker = out.stdout.rfind("@@RESULT@@")
    assert marker >= 0
    result = json.loads(out.stdout[marker + len("@@RESULT@@"):])
    assert result["source_id"] == "WO-4471"


def test_garbage_input_flags_review():
    result = parse_work_order("From: a@b.c\nSubject: hi\n\nno labels here")
    assert result["site_address"] == ""
    assert "missing site_address" in result["warnings"]
    assert result["needs_review"]
