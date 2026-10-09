import os
import sys
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
warnings.filterwarnings("ignore", category=UserWarning)

from src.evaluate import read_jsonl  # noqa: E402
from src.network import load_case  # noqa: E402

CASE = ROOT / "data" / "pglib_opf_case30_ieee.m"
EX_REQ = ROOT / "data" / "requests.example.jsonl"
EX_GT = ROOT / "data" / "ground_truth.example.jsonl"
REQ = ROOT / "data" / "requests.jsonl"
GT = ROOT / "data" / "ground_truth.jsonl"


@pytest.fixture(scope="session")
def net():
    return load_case(str(CASE))


def benchmark_paths():
    """The researcher's benchmark if it exists, else the example files."""
    if REQ.exists() and GT.exists():
        return REQ, GT
    return EX_REQ, EX_GT


@pytest.fixture(scope="session")
def benchmark():
    rp, gp = benchmark_paths()
    return read_jsonl(rp), read_jsonl(gp)
