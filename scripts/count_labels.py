"""Step 7: count the researcher's my_label values per backend and mode.

Reads results/failures.csv (read-only), writes results/tables/failure_labels.md
and .csv, and one bar chart results/figures/failure_labels.png (+ CSV).
Rows without a label are counted as UNLABELED.
"""
import csv
import io

import _common  # noqa: F401
from _common import RESULTS

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.evaluate import to_markdown

# Reference palette (dataviz skill) categorical slots, fixed order
SLOTS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"


def main():
    raw = (RESULTS / "failures.csv").read_bytes()
    try:  # Excel on Windows may save the labelled file as cp1252 instead of UTF-8
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise SystemExit("failures.csv has no rows")
    df = pd.DataFrame(rows)
    df["my_label"] = df["my_label"].fillna("").str.strip().replace("", "UNLABELED")
    df["series"] = df["backend"] + " M" + df["mode"].astype(str)
    table = df.pivot_table(index="my_label", columns="series", values="request_id", aggfunc="count", fill_value=0)
    table = table.loc[table.sum(axis=1).sort_values(ascending=False).index]
    (RESULTS / "tables").mkdir(parents=True, exist_ok=True)
    table.to_csv(RESULTS / "tables" / "failure_labels.csv")
    (RESULTS / "tables" / "failure_labels.md").write_text(to_markdown(table.reset_index()), encoding="utf-8")

    series = list(table.columns)
    if len(series) > len(SLOTS):
        raise SystemExit("more than 8 backend/mode series: plot fewer at a time")
    fig, ax = plt.subplots(figsize=(8, 4), dpi=150)
    x = np.arange(len(table.index))
    w = 0.8 / len(series)
    for k, s in enumerate(series):
        ax.bar(x + (k - (len(series) - 1) / 2) * w, table[s].to_numpy(), w * 0.92, color=SLOTS[k], label=s)
    ax.set_xticks(x, table.index, rotation=30, ha="right", fontsize=8)
    ax.set_ylabel("failed attempts", color=INK2)
    ax.set_title("Failure labels by backend and mode", loc="left", fontsize=11, fontweight="bold", color=INK)
    ax.grid(axis="y", color=GRID)
    ax.set_axisbelow(True)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.legend(frameon=False, fontsize=8, ncols=min(4, len(series)))
    fig.tight_layout()
    (RESULTS / "figures").mkdir(parents=True, exist_ok=True)
    fig.savefig(RESULTS / "figures" / "failure_labels.png")
    table.to_csv(RESULTS / "figures" / "failure_labels.csv")
    print(table.to_string())


if __name__ == "__main__":
    main()
