"""Phase 7 load-blend tests.

Seam: the pure ``load`` module. ``srpe_load`` scales a Borg CR10 rating into
Garmin-load units; ``blend`` fuses that with the device load per discipline.
No DB, no I/O - just the functions.
"""

from __future__ import annotations

from garmin_coach import load

SCALE = 0.3
SILA_DEFAULT = 7


def test_sila_default_rpe_maps_a_hard_session_near_hard_te_load():
    """A ~70-minute Siła session at the default RPE lands near hard_te_load (150)."""
    srpe = load.srpe_load("Siła", None, 4200, scale=SCALE, sila_default_rpe=SILA_DEFAULT)
    assert abs(srpe - 147.0) < 1e-6  # 0.3 * 7 * 70
    assert load.blend("Siła", 22.0, srpe) == srpe  # Garmin's 22 is discarded


def test_sila_uses_logged_rpe_over_the_default():
    srpe = load.srpe_load("Siła", 9, 4200, scale=SCALE, sila_default_rpe=SILA_DEFAULT)
    assert abs(srpe - 189.0) < 1e-6  # 0.3 * 9 * 70


def test_cardio_gets_no_default_rpe_when_unlogged():
    """A run with no logged RPE has no sRPE - the blend keeps the honest Garmin load."""
    srpe = load.srpe_load("Bieganie", None, 3600, scale=SCALE, sila_default_rpe=SILA_DEFAULT)
    assert srpe is None
    assert load.blend("Bieganie", 180.0, srpe) == 180.0


def test_cardio_rpe_only_raises_load_never_lowers_it():
    """max(garmin, sRPE): a logged RPE bumps a run up only when it exceeds Garmin."""
    srpe = load.srpe_load("Bieganie", 8, 3600, scale=SCALE, sila_default_rpe=SILA_DEFAULT)
    assert abs(srpe - 144.0) < 1e-6  # 0.3 * 8 * 60
    assert load.blend("Bieganie", 180.0, srpe) == 180.0  # Garmin higher -> unchanged
    assert load.blend("Bieganie", 100.0, srpe) == srpe    # sRPE higher -> raised


def test_null_garmin_load_degrades_to_srpe_or_zero():
    srpe = load.srpe_load("Bieganie", 6, 3600, scale=SCALE, sila_default_rpe=SILA_DEFAULT)
    assert load.blend("Bieganie", None, srpe) == srpe
    assert load.blend("Bieganie", None, None) == 0.0


def test_sila_without_duration_falls_back_to_garmin_load():
    """No duration -> no sRPE; Siła keeps the device load rather than dropping to 0."""
    srpe = load.srpe_load("Siła", None, None, scale=SCALE, sila_default_rpe=SILA_DEFAULT)
    assert srpe is None
    assert load.blend("Siła", 22.0, srpe) == 22.0
