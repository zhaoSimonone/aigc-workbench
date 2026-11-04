#!/usr/bin/env python3
"""Short CLI for AIGC Shelf: aigc login | list | download."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from assets import main


if __name__ == "__main__":
    sys.exit(main())
