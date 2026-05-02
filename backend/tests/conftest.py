"""Test session setup.

Several legacy test modules install lightweight stubs into ``sys.modules`` for
``backend`` / ``backend.services`` (and various submodules) at import time.
A few of those stubs use ``setdefault`` with ``__path__ = []`` which then
permanently breaks submodule auto-import for the rest of the pytest session.

To keep tests independent of collection order we eagerly import the real
``backend`` package (and ``backend.services``) here so they're cached in
``sys.modules`` BEFORE any test module gets a chance to install a stub.
After that, any ``sys.modules.setdefault(...)`` in a test file becomes a no-op
and the real package keeps its real ``__path__``.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _ensure_real_package(name: str, relative_path: str) -> None:
    if name in sys.modules:
        module = sys.modules[name]
        if hasattr(module, "__path__") and module.__path__:
            return
    backend_root = Path(__file__).resolve().parents[1]
    target = backend_root.parent / relative_path
    if target.is_dir():
        # Force a real import so __path__ is set correctly. If a stub is already
        # installed without __path__, drop it first so Python re-imports fresh.
        sys.modules.pop(name, None)
        __import__(name)


_ensure_real_package("backend", "backend")
_ensure_real_package("backend.services", "backend/services")

