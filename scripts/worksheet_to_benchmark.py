"""Turn the filled-in worksheet into the two benchmark files, then validate them.

Reads data/benchmark_worksheet.csv after the researcher has filled the `text`
and `note` columns, and writes data/requests.jsonl and data/ground_truth.jsonl.
It copies the researcher's words exactly; it never writes or changes wording.
Refuses to run while any row has an empty text or note, and refuses to overwrite
existing benchmark files unless --force is given.

Usage: python scripts/worksheet_to_benchmark.py [--force]
"""
import argparse
import csv
import json

import _common  # noqa: F401
from _common import REQUESTS, ROOT, TRUTH

from validate_benchmark import validate

WORKSHEET = ROOT / "data" / "benchmark_worksheet.csv"


def convert(rows):
    """Worksheet rows -> (requests, ground truth) lists. Raises ValueError on empty cells."""
    missing = [r["id"] for r in rows if not r["text"].strip() or not r["note"].strip()]
    if missing:
        raise ValueError(f"fill in text and note for: {', '.join(missing)}")
    reqs, truth = [], []
    for r in rows:
        reqs.append({"id": r["id"], "group": r["group"], "text": r["text"].strip()})
        item = {"id": r["id"], "refuse": r["group"] == "impossible",
                "edits": json.loads(r["edits"] or "[]"), "note": r["note"].strip()}
        if r["probe_edits"].strip():
            item["probe_edits"] = json.loads(r["probe_edits"])
        truth.append(item)
    return reqs, truth


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--worksheet", default=str(WORKSHEET))
    ap.add_argument("--force", action="store_true", help="overwrite existing benchmark files")
    a = ap.parse_args()
    if (REQUESTS.exists() or TRUTH.exists()) and not a.force:
        raise SystemExit("data/requests.jsonl or data/ground_truth.jsonl exists; use --force to overwrite")
    with open(a.worksheet, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    try:
        reqs, truth = convert(rows)
    except ValueError as exc:
        raise SystemExit(str(exc))
    for path, items in ((REQUESTS, reqs), (TRUTH, truth)):
        with open(path, "w", encoding="utf-8") as f:
            for it in items:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")
    print(f"wrote {REQUESTS} and {TRUTH} ({len(reqs)} items)\n")
    lines, flags = validate(REQUESTS, TRUTH)
    print("\n".join(lines))
    raise SystemExit(1 if flags else 0)


if __name__ == "__main__":
    main()
