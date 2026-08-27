"""Enforce independent statement and branch coverage thresholds."""

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--min-statements", type=float, default=90.0)
    parser.add_argument("--min-branches", type=float, default=85.0)
    args = parser.parse_args()
    totals = json.loads(args.report.read_text(encoding="utf-8"))["totals"]
    statements = float(totals["percent_statements_covered"])
    branches = float(totals["percent_branches_covered"])
    print(f"organization coverage: statements={statements:.2f}% branches={branches:.2f}%")
    failures = []
    if statements < args.min_statements:
        failures.append(f"statement coverage {statements:.2f}% < {args.min_statements:.2f}%")
    if branches < args.min_branches:
        failures.append(f"branch coverage {branches:.2f}% < {args.min_branches:.2f}%")
    if failures:
        raise SystemExit("; ".join(failures))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
