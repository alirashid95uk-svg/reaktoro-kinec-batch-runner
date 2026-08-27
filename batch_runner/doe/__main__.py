"""Command-line entry point for standalone batch-runner DoE workflows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .launch import launch_all, launch_sample
from .package import generate_design


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m batch_runner.doe",
        description="Generate and launch reproducible batch-runner DoE designs.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="Generate one immutable design package.")
    generate.add_argument("spec", type=Path, help="DoE YAML specification.")
    generate.add_argument("output_dir", type=Path, help="Fresh design-package directory.")

    launch = subparsers.add_parser("launch", help="Launch one accepted sample.")
    launch.add_argument("manifest", type=Path, help="Design manifest or package directory.")
    launch.add_argument("sample_id", help="Accepted sample ID, e.g. sample-000001.")
    launch.add_argument("--preflight-only", action="store_true")
    launch.add_argument("--events-jsonl", action="store_true")

    launch_all_parser = subparsers.add_parser(
        "launch-all", help="Launch all accepted samples in sample order."
    )
    launch_all_parser.add_argument(
        "manifest", type=Path, help="Design manifest or package directory."
    )
    launch_all_parser.add_argument("--preflight-only", action="store_true")
    launch_all_parser.add_argument("--events-jsonl", action="store_true")
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "generate":
        package = generate_design(args.spec, args.output_dir)
        print(package)
        return
    if args.command == "launch":
        result = launch_sample(
            args.manifest,
            args.sample_id,
            preflight_only=args.preflight_only,
            events_jsonl=args.events_jsonl,
        )
        print(json.dumps(result, sort_keys=True, indent=2, default=str))
        return
    results = launch_all(
        args.manifest,
        preflight_only=args.preflight_only,
        events_jsonl=args.events_jsonl,
    )
    print(json.dumps(results, sort_keys=True, indent=2, default=str))


if __name__ == "__main__":
    main()
