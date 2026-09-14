"""Meeting prep — the week's jobs in, one-page tech briefs out.

Pipeline:
    ingest jobs (JSON export, or browser adapter for API-less FSMs)
    -> normalize (sandbox) -> rule-based flags -> render per-tech briefs
    + shop summary (Markdown + printable HTML) -> deliver to Slack

Solari primitives: SANDBOX for untrusted job data; CLOUD BROWSER for
field-service systems with no API (fixture page in mock mode).

Flags are rules with job_id citations — a manager can verify every claim.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from core.audit import RunLog
from core.config import load_yaml, repo_root
from core.drivers import as_driver
from core.notify import SlackNotifier

from .flags import flag_job
from .normalize_worker import MARKER
from .render import shop_summary_md, tech_brief_md, to_html

WORKER = Path(__file__).parent / "normalize_worker.py"


def _cfg() -> dict:
    return load_yaml(repo_root() / "config" / "meeting_prep.yaml")


async def _ingest_browser(core, cfg: dict, run_log: RunLog) -> list[dict]:
    """Browser adapter path: scrape the FSM's completed-jobs page."""
    if cfg.get("mode") == "mock" and hasattr(core, "use_pages"):
        core.use_pages({"fsm.example": (repo_root() /
                        "fixtures/fsm_jobs/jobs_page.html").read_text()})
    session, page = await core.browser(
        profile_name=cfg.get("profile"), recording=True)
    run_log.session("browser", session.id)
    t0 = time.monotonic()
    try:
        driver = as_driver(page)
        await driver.goto(cfg.get("jobs_url", "https://fsm.example/jobs/done"))
        html = await driver.content()
    finally:
        await session.close()
        run_log.usage("browser", session.id, time.monotonic() - t0)
        run_log.session("browser", session.id,
                        replay_url=await core.replay_url(session.id))
    sel = {"row": r'<tr class="job-row"[^>]*>(.*?)</tr>',
           "cell": r'<td class="j-([a-z_]+)"[^>]*>([^<]*)</td>',
           **(cfg.get("selectors") or {})}
    jobs = []
    for row in re.findall(sel["row"], html, re.S):
        cells = {k: v.strip() for k, v in re.findall(sel["cell"], row)}
        jobs.append({"job_id": cells.get("id", ""), "tech": cells.get("tech", ""),
                     "summary": cells.get("summary", ""),
                     "status": cells.get("status", "")})
    return jobs


async def run(core, run_log: RunLog, cfg: dict | None = None) -> dict:
    cfg = {**_cfg(), **(cfg or {})}

    # -- ingest ------------------------------------------------------------------
    if cfg.get("source", "json") == "browser":
        raw = await _ingest_browser(core, cfg, run_log)
        run_log.step("ingest", count=len(raw), source="browser")
        src_desc = cfg.get("jobs_url", "browser")
    else:
        path = repo_root() / cfg.get("jobs", "fixtures/fsm_jobs/jobs.json")
        raw = json.loads(path.read_text())
        run_log.step("ingest", count=len(raw), source=str(cfg.get("jobs")))
        src_desc = str(cfg.get("jobs"))

    # -- normalize in the sandbox ------------------------------------------------
    if cfg.get("mode") != "live" or cfg.get("source") == "browser":
        # browser path has no raw file — write the scrape to a temp input
        import tempfile
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(raw, tmp); tmp.close()
        input_path = Path(tmp.name)
    else:
        input_path = repo_root() / cfg.get("jobs", "fixtures/fsm_jobs/jobs.json")
    t0 = time.monotonic()
    stdout = await core.run_python_in_sandbox(
        str(WORKER), {str(input_path): "/tmp/jobs.json"})
    run_log.usage("sandbox", "normalize-jobs", time.monotonic() - t0)
    idx = stdout.rfind(MARKER)
    jobs = json.loads(stdout[idx + len(MARKER):])
    run_log.step("normalize", jobs=len(jobs))

    # -- flags -------------------------------------------------------------------
    flags = [f for j in jobs for f in flag_job(j)]
    run_log.step("flag", count=len(flags),
                 kinds=sorted({f["flag"] for f in flags}))

    # -- render ------------------------------------------------------------------
    out_dir = repo_root() / "out"
    out_dir.mkdir(exist_ok=True)
    written = []
    techs = sorted({j.get("tech", "?") for j in jobs})
    for tech in techs:
        md = tech_brief_md(tech, jobs, flags)
        md_path = out_dir / f"brief-{tech}-{run_log.run_id}.md"
        html_path = md_path.with_suffix(".html")
        md_path.write_text(md)
        html_path.write_text(to_html(md, f"tech brief — {tech}"))
        written += [str(md_path), str(html_path)]
    summary = shop_summary_md(jobs, flags)
    smd = out_dir / f"shop-summary-{run_log.run_id}.md"
    smd.write_text(summary)
    (out_dir / f"shop-summary-{run_log.run_id}.html").write_text(
        to_html(summary, "shop summary"))
    written += [str(smd)]
    run_log.step("render", briefs=len(techs), source=src_desc,
                 files=[Path(p).name for p in written])

    # -- deliver (internal digest — not customer-facing) -------------------------
    res = await SlackNotifier().send(
        ":clipboard: Meeting prep ready — "
        + ", ".join(f"{t}: {len([f for f in flags if f['job_id'] in {j['job_id'] for j in jobs if j.get('tech')==t}])} flags"
                    for t in techs))
    run_log.step("notify_slack", status=res.status)

    run_log.finish("ok", techs=len(techs), flags=len(flags), files=written)
    return {"techs": techs, "flags": flags, "files": written}
