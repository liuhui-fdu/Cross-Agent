"""Command line entry points."""

from __future__ import annotations

import argparse
import json

from cross_agent.config import load_settings
from cross_agent.evaluation import run_actmem_eval


def main() -> None:
    parser = argparse.ArgumentParser(description="Cross-Agent utilities")
    parser.add_argument(
        "command",
        choices=["eval"],
        help="Command to run.",
    )
    parser.add_argument(
        "--config",
        default="configs/default.json",
        help="Path to JSON config file.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Evaluation item limit.")
    args = parser.parse_args()

    settings = load_settings(args.config)
    if args.command == "eval":
        result = run_actmem_eval(settings, limit=args.limit)
        print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
