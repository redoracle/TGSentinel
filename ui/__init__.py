"""UI package shim for test and runtime imports.

This package primarily exists so that:

  * `import app` can load the Flask UI module directly from `ui/app.py`
    when the `ui/` directory is on `sys.path` (as done in tests), and
  * `import ui.app` and subsequent references to `ui.app` resolve to the
    very same module object, so that patches applied via `ui.app` are
    visible to the running Flask views.
"""

from __future__ import annotations

import importlib
import sys as _sys

# Ensure that importing ``ui.app`` always resolves to the same module
# object as a bare ``import app`` when the UI directory is on sys.path.
try:  # pragma: no cover - import glue
    _app = importlib.import_module("app")
    _sys.modules.setdefault("ui.app", _app)
except Exception:
    # In environments where ``app`` cannot be imported yet (e.g. tooling
    # importing ``ui`` very early), we simply leave the alias unset.
    _app = None  # type: ignore[assignment]

# Also expose the app module as an attribute of the ``ui`` package so that
# patch targets like ``patch('ui.app.load_config')`` work as expected.
if _app is not None:
    app = _app  # type: ignore[assignment]
