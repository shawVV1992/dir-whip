"""Single YAML read entry point for the plugin core -- backend-detecting ``load_file``.

Read a file once, then parse with ruamel.yaml (YAML 1.1, safe, last-value-
wins) when installed, else PyYAML (the 0.8.5 behavior), else fail open.
Third-party imports stay function-local so a missing or broken YAML
package can never break ``import dirwhip``; missing/unreadable/invalid
files log and return None, and duplicate keys keep PyYAML's last-value-
wins while warning (path + line).

Layer: core
Refs: spec 5.6
Key exports:
  - load_file -- parse a YAML file; None on missing/unreadable/invalid/no backend. Never raises.
"""

import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger("dir-whip")

__all__ = ["load_file"]


def _detect_backend():
    """First installed reader: "ruamel", "pyyaml" or None.

    Probed on every call (no module-level cache), so a runtime install is
    visible and tests can reset/force the selection. Any exception from
    the probe (not just ImportError) counts as unavailable: a broken
    install must not crash the plugin again.
    """
    try:
        import ruamel.yaml  # noqa: F401 -- probe only
    except Exception as exc:
        if not isinstance(exc, ImportError):
            logger.debug("dir-whip: ruamel.yaml unavailable: %s", exc)
    else:
        return "ruamel"
    try:
        import yaml  # noqa: F401 -- probe only
    except Exception as exc:
        if not isinstance(exc, ImportError):
            logger.debug("dir-whip: PyYAML unavailable: %s", exc)
        return None
    return "pyyaml"


def _last_wins_constructor(sink):
    """SafeConstructor subclass: last value wins, duplicates recorded.

    Built lazily because the ruamel import must stay function-local; the
    caller owns the per-parse sink.
    """
    from ruamel.yaml.constructor import SafeConstructor

    class _LastWinsSafeConstructor(SafeConstructor):
        def check_mapping_key(self, node, key_node, mapping, key, value):
            if key in mapping:
                sink.append((key, key_node.start_mark.line + 1))
            return True

    return _LastWinsSafeConstructor


def _ruamel_load(text):
    """Parse with ruamel.yaml (YAML 1.1, safe); -> (data, duplicates)."""
    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError

    def _attempt(pure):
        sink = []
        parser = YAML(typ="safe", pure=pure)
        parser.version = (1, 1)
        parser.Constructor = _last_wins_constructor(sink)
        return parser.load(text), sink

    try:
        return _attempt(False)
    except (ImportError, YAMLError):
        # C parser rejects some docs PyYAML accepts, or clib is absent:
        # retry with the pure-Python parser (same policy as Hermes).
        return _attempt(True)


def _pyyaml_load(text):
    """Parse with PyYAML (its own last-wins); -> (data, [])."""
    import yaml

    return yaml.safe_load(text), []


@lru_cache(maxsize=1)
def _warn_no_backend():
    """One WARNING per process when neither backend is importable."""
    logger.warning(
        "dir-whip: no YAML backend available (ruamel.yaml / PyYAML); "
        "config guard disabled"
    )


def load_file(path):
    """Parse a YAML file; None on missing/unreadable/invalid/no backend.

    Reads the file once as UTF-8, then parses with the first available
    backend (ruamel -> PyYAML). A parse error is NOT retried with another
    backend: the same file must never yield two different results.
    Duplicate keys keep PyYAML's last-value-wins and warn per key.
    Never raises.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("dir-whip: cannot read YAML config %s: %s", path, exc)
        return None

    backend = _detect_backend()
    if backend is None:
        _warn_no_backend()
        return None

    try:
        if backend == "pyyaml":
            data, duplicates = _pyyaml_load(text)
        else:
            data, duplicates = _ruamel_load(text)
    except Exception as exc:
        logger.warning("dir-whip: cannot parse YAML config %s: %s", path, exc)
        return None

    for key, line in duplicates:
        logger.warning(
            "dir-whip: duplicate YAML key %r in %s (line %s); "
            "last value wins",
            key,
            path,
            line,
        )
    return data
