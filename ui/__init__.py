"""UI package shim for test and runtime imports.

This package primarily exists so that:

  * `import app` can load the Flask UI module directly from `ui/app.py`
    when the `ui/` directory is on `sys.path` (as done in tests), and
  * `import ui.app` and subsequent references to `ui.app` resolve to the
    very same module object, so that patches applied via `ui.app` are
    visible to the running Flask views.
"""

from __future__ import annotations

import sys as _sys

# If the top-level module ``app`` is already loaded and points at our
# `ui/app.py`, alias it as ``ui.app`` so there is a single shared module
# instance regardless of how it was imported.
_top = _sys.modules.get("app")
if _top is not None:
    _top_file = getattr(_top, "__file__", "") or ""
    # Normalize path for cross-platform compatibility
    import os

    _top_file_normalized = _top_file.replace("\\", "/")
    if _top_file_normalized.endswith("/ui/app.py") and "ui.app" not in _sys.modules:
        _sys.modules["ui.app"] = _top

# Expose the app submodule as an attribute when available. This ensures
# that ``import ui.app`` followed by ``ui.app`` works even when the
# module was originally imported as plain ``app``.
_sub = _sys.modules.get("ui.app")
if _sub is not None:
    app = _sub  # type: ignore[assignment]
