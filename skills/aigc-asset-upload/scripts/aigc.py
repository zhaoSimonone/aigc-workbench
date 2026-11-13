#!/usr/bin/env python3
"""Short CLI for AIGC Shelf, Baidu Netdisk and COS."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from assets import main


if __name__ == "__main__":
    sys.exit(main())
