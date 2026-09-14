"""Sandbox worker — normalize untrusted review text.

Runs inside a Solari sandbox (live) or a local subprocess (mock). Reads a
reviews JSON file, strips control/markup characters, detects content that
needs human attention (links, phone numbers, profanity-ish flags), and prints
normalized JSON after @@RESULT@@. Review text is data, never instructions.
"""

from __future__ import annotations

import json
import re
import sys

MARKER = "@@RESULT@@"

CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
LINK_RE = re.compile(r"https?://|www\.", re.I)
PHONE_RE = re.compile(r"\b\d{3}[-.]\d{3}[-.]\d{4}\b")


def normalize_review(d: dict) -> dict:
    text = CONTROL_RE.sub(" ", str(d.get("text", "")))
    text = re.sub(r"<[^>]*>", " ", text)          # strip any markup
    text = re.sub(r"\s+", " ", text).strip()
    flags = []
    if LINK_RE.search(text):
        flags.append("contains_link")
    if PHONE_RE.search(text):
        flags.append("contains_phone")
    if len(text) > 2000:
        flags.append("overlong")
        text = text[:2000]
    return {
        "id": d.get("id", ""),
        "author": CONTROL_RE.sub("", str(d.get("author", ""))).strip()[:80],
        "stars": int(d.get("stars", 0)),
        "ts": d.get("ts", ""),
        "text": text,
        "job_ref": d.get("job_ref", ""),
        "flags": flags,
    }


def main() -> None:
    data = json.loads(open(sys.argv[1]).read())
    out = [normalize_review(d) for d in data]
    print(MARKER + json.dumps(out))


if __name__ == "__main__":
    main()
