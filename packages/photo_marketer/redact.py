"""Redaction hook — the extension point for faces, plates, addresses, paperwork.

Set `redaction_hook` in config/photo_marketer.yaml to a Python file that
defines:

    def redact(png_bytes: bytes) -> bytes

It runs inside the sandbox media worker on every image, AFTER metadata
stripping. The shipped default is a no-op — wire in your own detector
(blur boxes, OCR redaction, whatever your shop's policy requires). Output
images are already metadata-stripped by default.
"""

from __future__ import annotations


def redact(png_bytes: bytes) -> bytes:
    """Default: no-op. Replace with your shop's redaction policy."""
    return png_bytes
