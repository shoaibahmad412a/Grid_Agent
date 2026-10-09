"""Network model: load a MATPOWER case into a copyable Network and describe it.

Numbering: the operator (and the LLM) only ever sees file bus numbers (1-30),
generator ids G1..Gn and branch ids L1..Lm in file row order. Internal 0-based
array indices are used inside src/ only.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass

import numpy as np
from matpowercaseframes import CaseFrames


@dataclass
class Network:
    """DC network data. All arrays are in file row order.

    Units: power in MW, cost coefficients in $/h per MW^k, reactance in p.u.
    (on baseMVA), phase shift in degrees (as in the file), angles in rad.
    """
    baseMVA: float
    # buses
    bus_num: np.ndarray      # int, file bus numbers
    bus_type: np.ndarray     # int, 1=PQ 2=PV 3=ref
    pd: np.ndarray           # MW
    gs: np.ndarray           # MW consumed at V=1 p.u. (shunt conductance)
    # generators (G1..Gn)
    gen_bus: np.ndarray      # int, file bus numbers
    pmin: np.ndarray         # MW
    pmax: np.ndarray         # MW
    gen_status: np.ndarray   # int 0/1
    c2: np.ndarray           # $/MW^2h
    c1: np.ndarray           # $/MWh
    c0: np.ndarray           # $/h
    pmin_file: np.ndarray    # MW, never edited (used when a unit returns to service)
    pmax_file: np.ndarray    # MW, nameplate, never edited
    # branches (L1..Lm)
    br_from: np.ndarray      # int, file bus numbers
    br_to: np.ndarray        # int, file bus numbers
    br_x: np.ndarray         # p.u.
    br_tap: np.ndarray       # ratio, 0 in the file is stored as 1
    br_xfmr: np.ndarray      # bool, True when the file gives a tap ratio or shift (transformer)
    br_shift: np.ndarray     # degrees
    rate_a: np.ndarray       # MW, 0 = no limit
    br_status: np.ndarray    # int 0/1
    ref_bus: int             # file bus number of the type-3 bus

    # ---- sizes and ids -------------------------------------------------
    @property
    def n_bus(self) -> int:
        return len(self.bus_num)

    @property
    def n_gen(self) -> int:
        return len(self.gen_bus)

    @property
    def n_branch(self) -> int:
        return len(self.br_from)

    @property
    def gen_ids(self) -> list[str]:
        return [f"G{k + 1}" for k in range(self.n_gen)]

    @property
    def line_ids(self) -> list[str]:
        return [f"L{k + 1}" for k in range(self.n_branch)]

    def copy(self) -> "Network":
        return copy.deepcopy(self)

    # ---- id <-> index (the only place this conversion happens) ----------
    def bus_index(self, bus: int) -> int:
        """File bus number -> 0-based row. Raises KeyError if unknown."""
        hits = np.flatnonzero(self.bus_num == bus)
        if len(hits) != 1:
            raise KeyError(f"bus {bus}")
        return int(hits[0])

    def gen_index(self, gen_id: str) -> int:
        """'G3' -> 2. Raises KeyError if unknown."""
        return _id_index(gen_id, "G", self.n_gen)

    def line_index(self, line_id: str) -> int:
        """'L12' -> 11. Raises KeyError if unknown."""
        return _id_index(line_id, "L", self.n_branch)

    def has_bus(self, bus) -> bool:
        return isinstance(bus, int) and not isinstance(bus, bool) and bool(np.any(self.bus_num == bus))

    def susceptance(self) -> np.ndarray:
        """Branch series susceptance b = 1 / (x * tap), in p.u."""
        return 1.0 / (self.br_x * self.br_tap)

    def gen_bus_index(self) -> np.ndarray:
        """0-based bus row of every generator."""
        return np.array([self.bus_index(int(b)) for b in self.gen_bus], dtype=int)

    def total_load_mw(self) -> float:
        """Total demand Pd + Gs in MW."""
        return float(self.pd.sum() + self.gs.sum())

    def total_capacity_mw(self) -> float:
        """Sum of Pmax of in-service generators, MW."""
        return float(self.pmax[self.gen_status == 1].sum())


def _id_index(text, prefix: str, n: int) -> int:
    if not isinstance(text, str):
        raise KeyError(str(text))
    m = re.fullmatch(prefix + r"(\d+)", text.strip())
    if not m or not 1 <= int(m.group(1)) <= n:
        raise KeyError(text)
    return int(m.group(1)) - 1


def load_case(path: str) -> Network:
    """Load a MATPOWER case file (via matpowercaseframes) into a Network.

    Units in the result: MW for Pd/Pg limits/ratings, p.u. for x, degrees for
    the phase shift. A tap ratio of 0 in the file is stored as 1. Only
    polynomial gencost (model 2) with up to 3 coefficients is supported.
    """
    cf = CaseFrames(path)
    bus, gen, br, gc = cf.bus, cf.gen, cf.branch, cf.gencost
    base = float(cf.baseMVA)

    if not (gc["MODEL"] == 2).all():
        raise ValueError("only polynomial gencost (model 2) is supported")
    coef_cols = [c for c in gc.columns if re.fullmatch(r"C\d+", c)]
    if len(coef_cols) > 3:
        raise ValueError("gencost above quadratic is not supported")
    get = lambda c: gc[c].to_numpy(float) if c in gc.columns else np.zeros(len(gc))

    tap = br["TAP"].to_numpy(float).copy()
    xfmr = (tap != 0) | (br["SHIFT"].to_numpy(float) != 0)
    tap[tap == 0] = 1.0
    types = bus["BUS_TYPE"].to_numpy(int)
    refs = bus["BUS_I"].to_numpy(int)[types == 3]
    if len(refs) != 1:
        raise ValueError(f"expected one reference bus, found {len(refs)}")

    pmin = gen["PMIN"].to_numpy(float)
    pmax = gen["PMAX"].to_numpy(float)
    return Network(
        baseMVA=base,
        bus_num=bus["BUS_I"].to_numpy(int),
        bus_type=types,
        pd=bus["PD"].to_numpy(float),
        gs=bus["GS"].to_numpy(float),
        gen_bus=gen["GEN_BUS"].to_numpy(int),
        pmin=pmin.copy(),
        pmax=pmax.copy(),
        gen_status=(gen["GEN_STATUS"].to_numpy(float) > 0).astype(int),
        c2=get("C2"), c1=get("C1"), c0=get("C0"),
        pmin_file=pmin.copy(),
        pmax_file=pmax.copy(),
        br_from=br["F_BUS"].to_numpy(int),
        br_to=br["T_BUS"].to_numpy(int),
        br_x=br["BR_X"].to_numpy(float),
        br_tap=tap,
        br_xfmr=xfmr,
        br_shift=br["SHIFT"].to_numpy(float),
        rate_a=br["RATE_A"].to_numpy(float),
        br_status=(br["BR_STATUS"].to_numpy(float) > 0).astype(int),
        ref_bus=int(refs[0]),
    )


def network_card(net: Network) -> str:
    """Compact plain-text description of the grid for an LLM prompt.

    Operator numbering only (bus numbers, G1.., L1..). MW, $/MWh.
    """
    out = [f"GRID: {net.n_bus} buses, {net.n_gen} generators, {net.n_branch} branches. "
           f"Base {net.baseMVA:g} MVA. Total load {net.total_load_mw():.1f} MW."]
    out.append("\nBUS LOADS (MW), buses with nonzero load:")
    loads = [f"bus {b}: {p:g}" for b, p in zip(net.bus_num, net.pd) if p != 0]
    for i in range(0, len(loads), 6):
        out.append("  " + "; ".join(loads[i:i + 6]))
    out.append("\nGENERATORS (id, bus, status, Pmin-Pmax MW, cost):")
    for k, gid in enumerate(net.gen_ids):
        st = "in" if net.gen_status[k] else "out"
        cost = f"{net.c1[k]:.2f} $/MWh"
        if net.c2[k]:
            cost += f" + {net.c2[k]:g} $/MW^2h"
        note = " (no active power: synchronous condenser)" if net.pmax_file[k] == 0 else ""
        out.append(f"  {gid}: bus {net.gen_bus[k]}, {st}, {net.pmin[k]:g}-{net.pmax[k]:g} MW, {cost}{note}")
    out.append("\nBRANCHES (id: from-to, rating MW, status; T = transformer):")
    rows = []
    for k, lid in enumerate(net.line_ids):
        rate = "no limit" if net.rate_a[k] == 0 else f"{net.rate_a[k]:g}"
        t = " T" if net.br_xfmr[k] else ""
        st = "" if net.br_status[k] else " OUT"
        rows.append(f"{lid}: {net.br_from[k]}-{net.br_to[k]}, {rate}{t}{st}")
    for i in range(0, len(rows), 4):
        out.append("  " + "; ".join(rows[i:i + 4]))
    return "\n".join(out)
