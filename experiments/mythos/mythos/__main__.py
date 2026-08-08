"""`python -m mythos` で起動するためのエントリポイント。"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
