"""Figures for the note (PNG + the CSV of the plotted numbers) in results/figures/.

a. feasibility and edit accuracy by mode (grouped bars, error bars = std over repeats)
b. heatmap request group x mode, cell = success rate
c. energy and latency per decision by mode, with the DC-OPF solve for scale

Usage: python scripts/make_figures.py [--truth PATH] [--requests PATH]
"""
import argparse
import json

import _common  # noqa: F401
from _common import CASE, REQUESTS, RESULTS, TRUTH

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from src.evaluate import GROUPS, aggregate, load_runs, read_jsonl, score_all
from src.network import load_case

# Reference palette (dataviz skill), light mode
BLUE, ORANGE = "#2a78d6", "#eb6834"
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"
SEQ = matplotlib.colors.LinearSegmentedColormap.from_list("seq", ["#eef4fc", "#2a78d6", "#0d3b73"])
FIG = RESULTS / "figures"


def _style(ax, title, ylabel):
    ax.set_title(title, loc="left", color=INK, fontsize=11, fontweight="bold", pad=22)
    ax.set_ylabel(ylabel, color=INK2)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2)


def fig_a(by_mode, backend):
    t = by_mode[by_mode["backend"] == backend].sort_values("mode")
    modes = t["mode"].tolist()
    x = np.arange(len(modes))
    fig, ax = plt.subplots(figsize=(6.4, 3.8), dpi=150)
    w = 0.36
    for off, col, metric, label in ((-w / 2, BLUE, "feasibility_pct", "Feasibility"),
                                    (w / 2, ORANGE, "edit_accuracy", "Edit accuracy")):
        vals = t[f"{metric}_mean"].to_numpy(float)
        err = t[f"{metric}_std"].to_numpy(float)
        mask = ~np.isnan(vals)
        bars = ax.bar(x[mask] + off, vals[mask], w * 0.94, yerr=err[mask], color=col, label=label,
                      error_kw={"ecolor": INK2, "elinewidth": 1, "capsize": 3})
        for b, v in zip(bars, vals[mask]):
            ax.text(b.get_x() + b.get_width() / 2, v + 2, f"{v:.0f}", ha="center", va="bottom", fontsize=8, color=INK2)
    ax.set_xticks(x, [f"M{m}" for m in modes])
    ax.set_ylim(0, 112)
    _style(ax, f"Feasibility and edit accuracy by mode ({backend})", "% of requests")
    ax.legend(frameon=False, loc="lower left", bbox_to_anchor=(0, 1.0), ncols=2, fontsize=8)
    ax.text(0.99, -0.16, "M1 has no edits, so no edit accuracy. Error bars: std over repeats.",
            transform=ax.transAxes, ha="right", fontsize=7, color=INK2)
    fig.tight_layout()
    fig.savefig(FIG / f"a_feasibility_accuracy_{backend}.png")
    plt.close(fig)
    t[["backend", "mode", "feasibility_pct_mean", "feasibility_pct_std", "edit_accuracy_mean",
       "edit_accuracy_std"]].to_csv(FIG / f"a_feasibility_accuracy_{backend}.csv", index=False)


def fig_b(df, backend):
    d = df[df["backend"] == backend]
    modes = sorted(d["mode"].unique())
    grid = np.full((len(GROUPS), len(modes)), np.nan)
    for i, g in enumerate(GROUPS):
        for j, m in enumerate(modes):
            s = d[(d["group"] == g) & (d["mode"] == m)]["success"]
            if len(s):
                grid[i, j] = s.astype(float).mean() * 100
    fig, ax = plt.subplots(figsize=(5.2, 3.8), dpi=150)
    im = ax.imshow(grid, cmap=SEQ, vmin=0, vmax=100, aspect="auto")
    for i in range(len(GROUPS)):
        for j in range(len(modes)):
            v = grid[i, j]
            ax.text(j, i, "n/a" if np.isnan(v) else f"{v:.0f}%", ha="center", va="center", fontsize=9,
                    color="white" if not np.isnan(v) and v > 55 else INK)
    ax.set_xticks(range(len(modes)), [f"M{m}" for m in modes])
    ax.set_yticks(range(len(GROUPS)), GROUPS)
    ax.set_title(f"Success rate by request group and mode ({backend})", loc="left", fontsize=11,
                 fontweight="bold", color=INK)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(colors=INK2, length=0)
    cb = fig.colorbar(im, ax=ax, shrink=0.8)
    cb.outline.set_visible(False)
    cb.set_label("% success", color=INK2)
    fig.tight_layout()
    fig.savefig(FIG / f"b_group_mode_heatmap_{backend}.png")
    plt.close(fig)
    import pandas as pd
    pd.DataFrame(grid, index=GROUPS, columns=[f"M{m}" for m in modes]).to_csv(FIG / f"b_group_mode_heatmap_{backend}.csv")


def fig_c(by_mode, opf_energy):
    t = by_mode.sort_values(["backend", "mode"])
    labels = [f"{b}\nM{m}" for b, m in zip(t["backend"], t["mode"])] + ["DC-OPF\nsolve"]
    lat = list(t["latency_s_mean"]) + [opf_energy.get("seconds_per_solve", np.nan)]
    en = list(t["energy_kwh_per_decision_mean"]) + [opf_energy.get("energy_kwh_per_solve") or np.nan]
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 3.8), dpi=150)
    for ax, vals, title, unit in ((axes[0], en, "Energy per decision", "kWh (log scale)"),
                                  (axes[1], lat, "Latency per decision", "seconds (log scale)")):
        x = np.arange(len(labels))
        cols = [BLUE] * (len(labels) - 1) + [INK2]
        v = np.array(vals, float)
        ok = ~np.isnan(v) & (v > 0)
        ax.bar(x[ok], v[ok], 0.6, color=[c for c, k in zip(cols, ok) if k])
        for xi, vi, k in zip(x, v, ok):
            if k:
                ax.text(xi, vi * 1.15, f"{vi:.2g}", ha="center", fontsize=7, color=INK2)
            else:
                ax.text(xi, 0.02, "n/a", ha="center", fontsize=7, color=INK2, transform=ax.get_xaxis_transform())
        ax.set_yscale("log")
        ax.set_xticks(x, labels, fontsize=7)
        _style(ax, title, unit)
    axes[0].text(0, -0.28, "API calls: energy not measurable (remote). DC-OPF in gray for scale.",
                 transform=axes[0].transAxes, fontsize=7, color=INK2)
    fig.tight_layout()
    fig.savefig(FIG / "c_energy_latency.png")
    plt.close(fig)
    import pandas as pd
    pd.DataFrame({"label": [l.replace("\n", " ") for l in labels], "energy_kwh": en, "latency_s": lat}) \
        .to_csv(FIG / "c_energy_latency.csv", index=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--truth", default=str(TRUTH))
    ap.add_argument("--runs", default=str(RESULTS / "runs"))
    a = ap.parse_args()
    FIG.mkdir(parents=True, exist_ok=True)
    net = load_case(str(CASE))
    df = score_all(load_runs(a.runs), read_jsonl(a.truth), net)
    if df.empty:
        raise SystemExit("no scored runs found in " + a.runs)
    by_mode = aggregate(df, ["backend", "mode"])
    for b in sorted(df["backend"].unique()):
        fig_a(by_mode, b)
        fig_b(df, b)
    e_path = RESULTS / "tables" / "opf_energy.json"
    opf_energy = json.loads(e_path.read_text()) if e_path.exists() else {}
    fig_c(by_mode, opf_energy)
    print(f"figures saved in {FIG}")


if __name__ == "__main__":
    main()
