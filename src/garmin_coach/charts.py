"""Phase 3 coach charts: render the two report PNGs from the mart.

Out of the test seam - validated by a live run, not unit tests. Reads already-read
``daily_metrics`` rows and writes PNGs. Never calls Garmin.
"""

from __future__ import annotations

import datetime as _dt
import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402


def _xnums(rows: list[dict]) -> list[float]:
    """Row dates as matplotlib date numbers (pairs with ``ax.xaxis_date()``)."""
    return [mdates.date2num(_dt.date.fromisoformat(r["date"])) for r in rows]


def _finish(fig: "plt.Figure", ax: "plt.Axes", path: pathlib.Path) -> None:
    """Shared legend/date-axis/save scaffold for both charts."""
    ax.legend(loc="best", fontsize=8)
    ax.xaxis_date()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def render_hrv_band(rows: list[dict], path: pathlib.Path) -> None:
    """Daily HRV with the personal baseline +/- 1 SD band and marked low nights."""
    dated = [r for r in rows if r.get("hrv") is not None]
    fig, ax = plt.subplots(figsize=(9, 4))
    if dated:
        xs = _xnums(dated)
        ys = [r["hrv"] for r in dated]
        baseline = next((r["hrv_baseline"] for r in dated if r.get("hrv_baseline") is not None), None)
        sd = next((r["hrv_sd"] for r in dated if r.get("hrv_sd") is not None), None)
        if baseline is not None and sd is not None:
            ax.axhspan(baseline - sd, baseline + sd, alpha=0.15, color="tab:green",
                       label="baseline +/- 1 SD")
            ax.axhline(baseline, color="tab:green", lw=1, ls="--", label="baseline")
        ax.plot(xs, ys, marker="o", color="tab:blue", label="nightly HRV")
        low = [r for r in dated if r.get("hrv_low_flag") == 1]
        if low:
            ax.scatter(_xnums(low), [r["hrv"] for r in low], color="tab:red", zorder=5,
                       label="low night")
    ax.set_title("HRV vs personal band")
    ax.set_ylabel("HRV (ms)")
    _finish(fig, ax, path)


def render_acwr(rows: list[dict], path: pathlib.Path, thresholds: dict[str, float]) -> None:
    """ACWR over time with comfort-zone lines and the unreliable stretch shaded."""
    dated = [r for r in rows if r.get("acwr") is not None]
    fig, ax = plt.subplots(figsize=(9, 4))
    if dated:
        xs = _xnums(dated)
        ys = [r["acwr"] for r in dated]
        ax.plot(xs, ys, marker="o", color="tab:blue", label="ACWR")
        for key, style in (("acwr_risk_low", ":"), ("acwr_sweet_hi", "--"),
                           ("acwr_risk_high", "-.")):
            ax.axhline(thresholds[key], color="tab:gray", lw=1, ls=style,
                       label=f"{key}={thresholds[key]}")
        unreliable = _xnums(
            [r for r in dated if (r.get("n_chronic") or 0) < thresholds["acwr_min_chronic_days"]]
        )
        if unreliable:
            ax.axvspan(min(unreliable), max(unreliable), alpha=0.10,
                       color="tab:orange", label="n_chronic < 28 (indicative)")
    ax.set_title("ACWR over time")
    ax.set_ylabel("acute:chronic")
    _finish(fig, ax, path)
