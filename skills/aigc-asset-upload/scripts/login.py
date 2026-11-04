#!/usr/bin/env python3
"""Authenticate the shared Shelf client without uploading any assets."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from assets import main as assets_main


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    return assets_main(["login", *argv])


if __name__ == "__main__":
    sys.exit(main())
