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
from types import ModuleType

_app: ModuleType | None = None


def _import_app_module():
    """Import the underlying ``app`` module lazily."""
    global _app

    if _app is None:
        _app = importlib.import_module("app")
        _sys.modules.setdefault("ui.app", _app)

    return _app


# Ensure that importing ``ui.app`` always resolves to the same module
# object as a bare ``import app`` when the UI directory is on sys.path.
try:  # pragma: no cover - import glue
    _app = _import_app_module()
except Exception:
    # In environments where ``app`` cannot be imported yet (e.g. tooling
    # importing ``ui`` very early), we leave the alias unset until it's
    # explicitly accessed via ``__getattr__``.
    _app = None  # type: ignore[assignment]
else:
    app = _app  # type: ignore[assignment]


def __getattr__(name: str):
    """Lazily expose ``app`` when accessed through ``ui.app``."""
    if name == "app":
        module = _import_app_module()
        current_module = _sys.modules[__name__]
        setattr(current_module, "app", module)
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
