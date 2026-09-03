#!/usr/bin/env python3
"""Mi Fitness Web UI & Testing Proxy (Flask).

Usage:
    python app.py
    python app.py --host 127.0.0.1 --port 8322 --backend-url http://127.0.0.1:8321
"""

import argparse
import os
import sys
from pathlib import Path

# Add repo src to sys.path so it works without install
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from mi_fitness_mcp.web import run_server


def main():
    parser = argparse.ArgumentParser(description="Mi Fitness Web UI & API Test Proxy (Flask)")
    parser.add_argument(
        "--host",
        default=os.environ.get("BIND_HOST", "127.0.0.1"),
        help="Host address to bind (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "8322")),
        help="Port to listen on (default: 8322)",
    )
    parser.add_argument(
        "--backend-url",
        default=os.environ.get("MI_FITNESS_API_URL", "http://127.0.0.1:8321"),
        help="Backend FastAPI URL (default: http://127.0.0.1:8321)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Run in Flask debug mode",
    )

    args = parser.parse_args()
    run_server(
        host=args.host,
        port=args.port,
        backend_url=args.backend_url,
        debug=args.debug,
    )


if __name__ == "__main__":
    main()
