"""Sandbox worker — score photos and strip metadata.

Runs inside a Solari sandbox (live) or a local subprocess (mock). argv[1] is
a manifest JSON:

    {"folders": {"job-x": {"before.png": "/tmp/photos/job-x/before.png", ...}},
     "min": "32x24"}

For each job folder: verify the before/after pair, score usability
(dimensions, file size), rewrite each PNG keeping only IHDR/IDAT/IEND (drops
EXIF, tEXt comments, GPS tags, anything embedded), optionally run a redact
hook, and print JSON after @@RESULT@@ — cleaned images come back base64 so
identical code runs in both modes.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import struct
import sys
from pathlib import Path

MARKER = "@@RESULT@@"
KEEP = {b"IHDR", b"IDAT", b"IEND"}


def png_dims(data: bytes) -> tuple[int, int]:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return (0, 0)
    w, h = struct.unpack(">II", data[16:24])
    return w, h


def strip_metadata(data: bytes) -> bytes:
    """Rewrite a PNG keeping only critical chunks — drops all metadata."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return data
    out = bytearray(data[:8])
    pos = 8
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        typ = data[pos + 4:pos + 8]
        end = pos + 8 + length + 4
        if typ in KEEP:
            out += data[pos:end]
        pos = end
    return bytes(out)


def process_file(name: str, data: bytes, min_w: int, min_h: int, redact) -> dict:
    w, h = png_dims(data)
    cleaned = strip_metadata(data)
    if redact:
        cleaned = redact(cleaned)
    return {
        "file": name, "bytes": len(data), "width": w, "height": h,
        "metadata_removed": len(cleaned) < len(data),
        "usable": bool(w) and w >= min_w and h >= min_h,
        "cleaned_b64": base64.b64encode(cleaned).decode(),
    }


def _load_redact(path: str):
    spec = importlib.util.spec_from_file_location("redact_mod", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, "redact", None)


def main() -> None:
    manifest_path = Path(sys.argv[1])
    manifest = json.loads(manifest_path.read_text())
    # Manifest paths are relative to the manifest's directory — that keeps
    # them valid whether the sandbox is a real VM (/tmp/...) or the mock's
    # remapped workdir.
    base = manifest_path.parent
    min_w, min_h = (int(x) for x in manifest.get("min", "32x24").split("x"))
    redact_path = manifest.get("redact") or ""
    redact = _load_redact(str(base / redact_path)) if redact_path else None

    results = []
    for folder, files in sorted(manifest.get("folders", {}).items()):
        names = {Path(n).stem.lower() for n in files}
        has_pair = (any("before" in n for n in names)
                    and any("after" in n for n in names))
        res = {"folder": folder, "pair": has_pair, "usable": has_pair,
               "photos": []}
        if not has_pair:
            res["reason"] = "no before/after pair"
        for name, rel in sorted(files.items()):
            if not name.lower().endswith((".png", ".jpg", ".jpeg")):
                continue
            ph = process_file(name, (base / rel).read_bytes(),
                              min_w, min_h, redact)
            ph["usable"] = ph["usable"] and has_pair
            res["photos"].append(ph)
        if has_pair and not any(p["usable"] for p in res["photos"]):
            res["usable"] = False
            res["reason"] = "all photos below min dimensions"
        results.append(res)
    print(MARKER + json.dumps(results))


if __name__ == "__main__":
    main()
