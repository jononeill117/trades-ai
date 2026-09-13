"""trades-ai core — shared plumbing for every use-case package.

Everything in here is generic: the Solari client wrapper, the run/audit log,
notification adapters, YAML config loading, and the driver abstractions that
let the same use case run in --mock mode (no keys) or --live mode (real Solari
sessions).
"""

__all__ = ["config", "audit", "solari_client", "mock_solari", "drivers", "notify"]
