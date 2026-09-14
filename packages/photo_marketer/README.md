# photo-marketer

Before/after job photos in → scored, metadata-stripped images → platform-specific captions → approval per post → queued to GBP/Facebook/Instagram.

## Pipeline

```
job photo folders -> sandbox: score + strip metadata (+ optional redaction
hook) -> per-platform captions -> approval gate (one request per platform)
-> publish via cloud browser composer
```

## Why Solari

- **Sandbox**: untrusted media files are processed in a disposable VM —
  scored, metadata-stripped, optionally redacted; cleaned images return
  base64 so the code path is identical mock and live.
- **Cloud browser**: platforms without a publish API get their web composer
  driven by a recorded cloud browser (fixture composer page in mock mode).

## Safety properties

- **Never auto-publishes** — one approval request per platform per job.
- **Metadata stripped by default** — PNGs are rewritten keeping only
  IHDR/IDAT/IEND; EXIF, GPS tags, and comments never leave the sandbox.
- **Redaction hook** — `redaction_hook` points at a Python file defining
  `redact(png_bytes) -> bytes` for faces, plates, addresses, paperwork. Runs
  inside the sandbox after stripping. Ships as a no-op — wire your policy in.
- **Platform-specific copy** — GBP, Facebook, and Instagram get different
  captions, not one caption pasted three times.

## Usability rules

A folder needs a before/after pair; each photo must meet `min_dimensions`.
Folders without a pair are reported skipped — never silently dropped.

## Config — `config/photo_marketer.yaml`

`photos`, `platforms`, `composer_url`, `profile`, `min_dimensions`,
`redaction_hook`, `shop_name`, `service_area`, `hashtags`.

## Cost profile

One sandbox per run + one browser session per approved post. See
`runs/cost-report.py`.

## Known limitations

- PNG only for dimensions/stripping; JPEGs pass through unstripped (flagged
  as such in scoring).
- The bundled composer target is fictional; real publishing needs
  `composer_url`, a logged-in profile, and selectors for your platform.
- No scheduling — approved posts publish immediately.
