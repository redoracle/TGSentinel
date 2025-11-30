"""Compatibility shim to expose the UI Flask app as a top-level module.

Tests and deployment scripts historically imported `app` directly even
though the actual implementation lives in `ui/app.py`. This module keeps
that import path working by re-exporting everything from the real module.
"""

from ui.app import *  # noqa: F401,F403 - re-export legacy symbols
