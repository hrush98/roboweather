#!/usr/bin/env python3
"""Portable end-of-session check for hosts that support a Stop hook."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from weather_trader.agent_loop import main


if __name__ == "__main__":
    raise SystemExit(main(["stop"]))
