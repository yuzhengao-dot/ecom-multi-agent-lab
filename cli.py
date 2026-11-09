"""Zero-install entry point: ``python cli.py run --user U1001``.

Adds ``src/`` to sys.path so the project can be run straight from a clone
before ``pip install -e .``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ecom_agents.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
