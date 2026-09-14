"""Photo marketer — before/after job photos in, queued posts out.

Pipeline:
    ingest job folders -> score + strip metadata (+ optional redaction hook)
    in a sandbox -> draft platform-specific captions -> approval gate
    per platform -> publish (cloud browser where no API exists)

Solari primitives: SANDBOX for media processing (untrusted image files in,
cleaned images out as base64 — identical code path mock and live); CLOUD
BROWSER for publishing to platforms that lack an API.

Never auto-publishes. Every post — GBP, Facebook, Instagram — is a separate
approval request carrying the exact caption and photo list.
"""

from __future__ import annotations

import base64
import json
import tempfile
import time
from pathlib import Path

from core.approvals import get_gate
from core.audit import RunLog
from core.config import load_yaml, repo_root
from core.drivers import as_driver

from .captions import variants
from .media_worker import MARKER

WORKER = Path(__file__).parent / "media_worker.py"


def _cfg() -> dict:
    return load_yaml(repo_root() / "config" / "photo_marketer.yaml")


async def _process_media(core, cfg: dict, run_log: RunLog) -> list[dict]:
    """Copy photos into a sandbox, score+strip them, write cleaned files to
    out/photo-marketer/."""
    photos_root = repo_root() / cfg.get("photos", "fixtures/photos")
    folders: dict[str, dict[str, str]] = {}
    file_map: dict[str, str] = {}
    for d in sorted(photos_root.iterdir()):
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            file_map[str(p)] = f"/tmp/photos/{d.name}/{p.name}"
            # manifest paths are relative to the manifest's dir (/tmp)
            folders.setdefault(d.name, {})[p.name] = f"photos/{d.name}/{p.name}"

    redact_hook = cfg.get("redaction_hook") or ""
    if redact_hook:
        file_map[str(repo_root() / redact_hook)] = "/tmp/redact_hook.py"
        redact_hook = "redact_hook.py"

    mf = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump({"folders": folders, "min": cfg.get("min_dimensions", "32x24"),
               "redact": redact_hook}, mf)
    mf.close()
    # argv order matters: the manifest is argv[1], photos follow.
    file_map = {mf.name: "/tmp/manifest.json", **file_map}

    t0 = time.monotonic()
    stdout = await core.run_python_in_sandbox(str(WORKER), file_map)
    run_log.usage("sandbox", "media-process", time.monotonic() - t0)
    i = stdout.rfind(MARKER)
    if i < 0:
        raise RuntimeError(f"media worker produced no marker: {stdout!r}")
    results = json.loads(stdout[i + len(MARKER):])

    out_root = repo_root() / "out" / "photo-marketer"
    out_root.mkdir(parents=True, exist_ok=True)
    for folder in results:
        for ph in folder["photos"]:
            dest = out_root / f"{folder['folder']}-{ph['file']}"
            dest.write_bytes(base64.b64decode(ph.pop("cleaned_b64")))
            ph["cleaned_path"] = str(dest)
    return results


async def run(core, run_log: RunLog, cfg: dict | None = None) -> dict:
    cfg = {**_cfg(), **(cfg or {})}
    mode = cfg.get("mode", "mock")
    gate = get_gate(mode, run_log)

    results = await _process_media(core, cfg, run_log)
    run_log.step("media", folders=len(results),
                 usable=sum(1 for r in results if r["usable"]),
                 stripped=sum(1 for r in results for p in r["photos"]
                              if p["metadata_removed"]))

    out = {"folders": len(results), "posted": 0, "denied": 0, "skipped": 0}
    platforms = cfg.get("platforms", ["gbp", "facebook", "instagram"])
    for folder in results:
        if not folder["usable"]:
            run_log.step("queue", status="skipped", folder=folder["folder"],
                         reason=folder.get("reason", "not usable"))
            out["skipped"] += 1
            continue
        caps = variants(folder["folder"], cfg.get("caption_detail", ""), cfg)
        for platform in platforms:
            text = caps.get(platform)
            if not text:
                continue
            approved = await gate.require(
                f"social.publish.{platform}", platform,
                f"Post {folder['folder']} photos to {platform}: {text[:90]}",
                payload={"folder": folder["folder"], "platform": platform,
                         "caption": text,
                         "photos": [p["file"] for p in folder["photos"]
                                    if p["usable"]]},
                requester="photo-marketer")
            if not approved:
                run_log.step("publish", status="skipped",
                             folder=folder["folder"], platform=platform,
                             reason="approval denied")
                out["denied"] += 1
                continue
            status = await _publish(core, cfg, platform, folder, text, run_log)
            run_log.step("publish", folder=folder["folder"],
                         platform=platform, detail=status)
            out["posted"] += 1

    run_log.finish("ok", **out)
    return out


async def _publish(core, cfg: dict, platform: str, folder: dict,
                   caption: str, run_log: RunLog) -> str:
    """Post through the platform's composer UI in a cloud browser.

    Only reached after an approved decision. Mock mode serves a fixture
    composer page through the same driver path.
    """
    url = cfg.get("composer_url", "https://social.example/compose")
    if cfg.get("mode") == "live" and "example" in url:
        return ("not published — no real platform configured "
                "(composer_url is the fixture domain); approval was granted "
                "but the publish boundary was not exercised")
    if cfg.get("mode") == "mock" and hasattr(core, "use_pages"):
        core.use_pages({"social.example": (repo_root() /
                        "fixtures/social/composer.html").read_text()})
    session, page = await core.browser(
        profile_name=cfg.get("profile"), recording=True)
    run_log.session("browser", session.id)
    t0 = time.monotonic()
    try:
        driver = as_driver(page)
        await driver.goto(url + f"?platform={platform}")
        await driver.fill("#caption", caption)
        await driver.click("#post-submit")
    finally:
        await session.close()
        run_log.usage("browser", session.id, time.monotonic() - t0)
        run_log.session("browser", session.id,
                        replay_url=await core.replay_url(session.id))
    return f"queued {platform} post for {folder['folder']}"
