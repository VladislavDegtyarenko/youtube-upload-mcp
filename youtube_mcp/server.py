from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_server = importlib.import_module("server")

__all__ = [name for name in dir(_server) if not name.startswith("__")]
globals().update({name: getattr(_server, name) for name in __all__})

if __name__ == "__main__":
    _server.mcp.run(transport="stdio")
