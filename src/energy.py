"""Energy measurement with CodeCarbon (offline mode, country from .env).

Local (Ollama) LLM calls are measured per call in kWh. Remote API calls get
None with the reason REMOTE_NOTE. The DC-OPF solve is measured by repeating
it many times inside one tracker, because a single solve (~10 ms) is far
below CodeCarbon's resolution. All values are estimates from CodeCarbon's
hardware power model (RAPL/GPU counters when available, else TDP-based).
"""
from __future__ import annotations

import logging
import os
import time

from dotenv import load_dotenv

REMOTE_NOTE = "remote; not measurable"


def _tracker():
    from codecarbon import OfflineEmissionsTracker
    load_dotenv()
    logging.getLogger("codecarbon").setLevel(logging.ERROR)
    return OfflineEmissionsTracker(
        country_iso_code=os.getenv("CODECARBON_COUNTRY", "PAK"),
        save_to_file=False, log_level="error", measure_power_secs=1,
        tracking_mode="machine",
    )


def measure(fn):
    """Run fn() under a CodeCarbon tracker. Returns (result, energy_kwh, note).

    energy_kwh is None (with the reason in note) if CodeCarbon fails.
    """
    try:
        tr = _tracker()
        tr.start()
    except Exception as exc:
        return fn(), None, f"codecarbon unavailable: {type(exc).__name__}"
    try:
        result = fn()
    finally:
        try:
            tr.stop()
            kwh = float(tr.final_emissions_data.energy_consumed)
            note = "codecarbon offline"
        except Exception as exc:
            kwh, note = None, f"codecarbon failed: {type(exc).__name__}"
    return result, kwh, note


def measure_opf_energy(net, repeats: int = 200) -> dict:
    """Energy (kWh) and time (s) of ONE DC-OPF solve, averaged over `repeats` solves."""
    from src.dcopf import solve_dcopf
    t0 = time.perf_counter()

    def run():
        for _ in range(repeats):
            solve_dcopf(net)

    _, kwh, note = measure(run)
    dt = time.perf_counter() - t0
    return {"energy_kwh_per_solve": None if kwh is None else kwh / repeats,
            "seconds_per_solve": dt / repeats, "repeats": repeats, "note": note}
