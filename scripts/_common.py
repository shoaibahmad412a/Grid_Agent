"""Shared paths for the scripts (makes `python scripts/x.py` work from the repo root)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CASE = ROOT / "data" / "pglib_opf_case30_ieee.m"
REQUESTS = ROOT / "data" / "requests.jsonl"
TRUTH = ROOT / "data" / "ground_truth.jsonl"
RESULTS = ROOT / "results"
