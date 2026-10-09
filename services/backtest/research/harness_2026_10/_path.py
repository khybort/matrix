"""Import matrix_shared.research from the repo on the host.

The host python has pandas but not the service stack (sqlalchemy, ...), and
`matrix_shared/__init__.py` imports the DB layer. matrix_shared.research needs
none of it, so when the real package cannot be imported a bare namespace
module stands in for `matrix_shared` and only the research subpackage loads.
Inside a service image the real package imports normally."""

import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SRC = Path(os.environ.get("MATRIX_SHARED_SRC", ROOT / "packages" / "python-shared" / "src"))

try:
    if "MATRIX_SHARED_SRC" in os.environ:
        raise ImportError
    import matrix_shared  # noqa: F401
except Exception:
    for k in [k for k in sys.modules if k == "matrix_shared" or k.startswith("matrix_shared.")]:
        del sys.modules[k]
    pkg = types.ModuleType("matrix_shared")
    pkg.__path__ = [str(SRC / "matrix_shared")]
    sys.modules["matrix_shared"] = pkg
