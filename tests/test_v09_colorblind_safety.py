# Copyright 2026 Jamie-Leigh Blake and Termin project contributors
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0

"""Phase 5a.5: colorblind-safety conformance for tailwind-default.

Per BRD #2 §6.4, first-party Presentation providers must use
colorblind-safe choices for any color that carries information —
severity (success / error / warning / info), status, highlight,
badge. The provider must communicate the same information through
luminance contrast, label, icon, or shape — never hue alone.

Two-tier coverage:

  1. Module unit tests — exercise the CVD simulation, WCAG
     contrast, and pairwise-distinguishability helpers in
     `termin_runtime.colorblind`.
  2. Conformance tests — assert the actual Tailwind classes used
     by `presentation.py` for severity rendering pass:
       (a) WCAG AA contrast (≥4.5:1) between text and background
           where both are declared on the same element;
       (b) pairwise distinguishability under deuteranopia,
           protanopia, and tritanopia simulation;
       (c) at least one non-color signal per severity (font weight,
           label, icon) — verified by inspecting MARK_STYLES.

The conformance tests are the load-bearing ones for BRD §6.4. The
unit tests gate the simulation library so a future palette change
can rely on it.
"""

from __future__ import annotations

import pytest

from termin_runtime.colorblind import (
    TAILWIND_HEX,
    contrast_ratio,
    cvd_distinguishable,
    hex_to_rgb,
    relative_luminance,
    simulate_cvd,
)


# ── Module unit tests ──

def test_hex_to_rgb_basic():
    assert hex_to_rgb("#000000") == (0, 0, 0)
    assert hex_to_rgb("#ffffff") == (255, 255, 255)
    assert hex_to_rgb("#ff8800") == (255, 136, 0)


def test_hex_to_rgb_tolerates_no_hash_and_case():
    assert hex_to_rgb("FF8800") == (255, 136, 0)
    assert hex_to_rgb("  #aabbcc  ") == (170, 187, 204)


def test_hex_to_rgb_three_digit_short_form():
    assert hex_to_rgb("#abc") == hex_to_rgb("#aabbcc")


def test_hex_to_rgb_rejects_malformed():
    with pytest.raises(ValueError):
        hex_to_rgb("#1234")
    with pytest.raises(ValueError):
        hex_to_rgb("not a color")


def test_relative_luminance_black_and_white():
    assert relative_luminance("#000000") == pytest.approx(0.0, abs=1e-6)
    assert relative_luminance("#ffffff") == pytest.approx(1.0, abs=1e-6)


def test_contrast_ratio_black_on_white():
    """Maximum contrast ratio is 21:1."""
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0, abs=0.01)


def test_contrast_ratio_same_color_is_one():
    assert contrast_ratio("#888888", "#888888") == pytest.approx(1.0, abs=1e-6)


def test_simulate_cvd_returns_hex():
    result = simulate_cvd("#ff0000", "deuteranopia")
    assert result.startswith("#") and len(result) == 7


def test_simulate_cvd_rejects_unknown_kind():
    with pytest.raises(ValueError):
        simulate_cvd("#ff0000", "tetrachromacy")


def test_simulate_cvd_red_under_deuteranopia_loses_red():
    """Deuteranopes lose green sensitivity; pure red shifts toward
    yellow/dark. The simulated R channel should be much closer to
    the simulated G channel than for a normal observer."""
    sim = hex_to_rgb(simulate_cvd("#ff0000", "deuteranopia"))
    # Under CVD, R and G should converge — not necessarily equal,
    # but within a fraction of normal range.
    assert abs(sim[0] - sim[1]) < 200, (
        f"Expected red/green to converge under deuteranopia; got {sim}"
    )


def test_cvd_distinguishable_distant_colors_pass():
    # Black vs white must be distinguishable under any CVD.
    for kind in ("deuteranopia", "protanopia", "tritanopia"):
        assert cvd_distinguishable("#000000", "#ffffff", kind)


def test_cvd_distinguishable_same_color_fails():
    for kind in ("deuteranopia", "protanopia", "tritanopia"):
        assert not cvd_distinguishable("#3b82f6", "#3b82f6", kind)


# ── Tailwind palette conformance ──

def test_tailwind_palette_covers_required_classes():
    """The class names referenced by presentation.py's severity
    rendering must all be present in the palette table. If a future
    palette pass adds a new class, this test fails until the
    palette is updated."""
    required = {
        "red-50", "red-100", "red-600", "red-800",
        "amber-50", "amber-100",
        "green-50", "green-200", "green-600", "green-800",
        "blue-100", "blue-500", "blue-600",
        "gray-100", "gray-700", "white",
    }
    missing = required - set(TAILWIND_HEX)
    assert not missing, f"Palette missing entries: {sorted(missing)}"


# ── Severity-row backgrounds: pairwise distinguishability under CVD ──

# The MARK_STYLES dict in presentation.py uses these class strings
# for severity-coded row backgrounds. Distinguishable means a viewer
# with that CVD type can still tell them apart.
#
# v0.9 Phase 5a.5: warning and success were strengthened from `-50`
# to `-100` to give CVD viewers enough luminance contrast to
# distinguish without hue. They also gained a left-border accent
# (border-l-4 border-{amber,green}-{500,600}) to add a shape signal —
# tested separately in test_severity_marks_carry_non_color_signal.
SEVERITY_ROW_BG = {
    "urgent": TAILWIND_HEX["red-50"],
    "critical": TAILWIND_HEX["red-100"],
    "warning": TAILWIND_HEX["amber-100"],
    "success": TAILWIND_HEX["green-100"],
}


@pytest.mark.parametrize("kind", ["deuteranopia", "protanopia", "tritanopia"])
def test_warning_distinguishable_from_success_under_cvd(kind):
    """Warning vs success is the canonical red-green failure mode.
    They sit on the row-background palette as amber-50 vs green-50,
    which should remain distinguishable since amber has a yellow
    component and green is in the green-yellow range."""
    a = SEVERITY_ROW_BG["warning"]
    b = SEVERITY_ROW_BG["success"]
    assert cvd_distinguishable(a, b, kind), (
        f"warning ({a}) and success ({b}) collapse under {kind}"
    )


@pytest.mark.parametrize("kind", ["deuteranopia", "protanopia", "tritanopia"])
def test_critical_distinguishable_from_urgent_under_cvd(kind):
    """Both are red shades. Critical is red-100 (more saturated);
    urgent is red-50. The luminance gap is the load-bearing
    distinguisher under deuteranopia/protanopia."""
    a = SEVERITY_ROW_BG["critical"]
    b = SEVERITY_ROW_BG["urgent"]
    assert cvd_distinguishable(a, b, kind), (
        f"critical ({a}) and urgent ({b}) collapse under {kind}"
    )


# ── Toast palette: text-on-bg contrast must meet WCAG AA ──

@pytest.mark.parametrize("fg,bg,label", [
    (TAILWIND_HEX["white"],   TAILWIND_HEX["red-700"],   "error toast"),
    (TAILWIND_HEX["white"],   TAILWIND_HEX["green-700"], "success toast"),
    (TAILWIND_HEX["red-800"], TAILWIND_HEX["red-50"],    "error flash"),
    (TAILWIND_HEX["green-800"], TAILWIND_HEX["green-50"], "success flash"),
])
def test_toast_text_meets_wcag_aa(fg, bg, label):
    """WCAG AA: normal text needs ≥4.5:1 contrast against its
    background. Toasts are short-lived, but accessibility
    requirements apply throughout."""
    ratio = contrast_ratio(fg, bg)
    assert ratio >= 4.5, (
        f"{label}: contrast {ratio:.2f}:1 below WCAG AA (4.5:1) for "
        f"fg={fg} on bg={bg}"
    )


# ── Non-color-only encoding: BRD §6.4 last paragraph ──

def test_severity_marks_carry_non_color_signal():
    """BRD §6.4 last paragraph: where a provider uses color to
    convey information, it must also signal the same information
    through luminance, shape, label, or icon — never through hue
    alone.

    For the v0.9 Phase 5a.5 mark palette, every label carries at
    least one of: a font-weight class (`font-semibold`, `font-bold`,
    `font-medium`) or a left-border shape signal (`border-l-4`).
    Pre-5a.5 the warning and success labels carried hue alone;
    they now carry a left border whose color also gives stronger
    luminance contrast to anchor CVD viewers.
    """
    from termin_runtime import presentation
    # Read source and inspect each MARK_STYLES entry. A more rigorous
    # alternative would be to import MARK_STYLES directly, but it's
    # a local dict inside _render_data_table; the source-string
    # check is robust against the surrounding code while still
    # catching regressions on the dict entries themselves.
    expected_signals = {
        "urgent": ["font-semibold"],
        "critical": ["font-bold", "border-l-4"],
        "warning": ["border-l-4"],
        "success": ["border-l-4", "font-medium"],
        "highlighted": ["font-semibold"],
    }
    src = open(presentation.__file__).read()
    for label, signals in expected_signals.items():
        snippet = f'"{label}":'
        assert snippet in src, f"MARK_STYLES missing {label} entry"
        # Find the dict-entry line and verify at least one
        # non-color signal is present.
        for line in src.splitlines():
            if snippet in line and "MARK_STYLES" not in line:
                # Match any of the declared signals so future
                # palette tweaks (e.g., swapping border-l-4 for an
                # icon class) don't break the test for the wrong
                # reason.
                if any(sig in line for sig in signals):
                    break
                pytest.fail(
                    f"{label!r} mark style has no non-color signal; "
                    f"expected one of {signals!r}; line: {line.strip()}"
                )
        else:
            pytest.fail(f"could not find MARK_STYLES line for {label!r}")


def test_warning_and_success_have_distinct_backgrounds_vs_white():
    """`warning` (amber-100) and `success` (green-100) row
    backgrounds must have a color-distinguishable presence against
    a white surface — the row mechanism's primary signal. Pre-5a.5
    these were `-50` shades whose contrast with white was ~1.04 —
    barely visible. v0.9 Phase 5a.5 strengthened them to `-100`
    which sits comfortably above 1.07 contrast."""
    for name in ("warning", "success"):
        bg = SEVERITY_ROW_BG[name]
        assert contrast_ratio(bg, "#ffffff") >= 1.07, (
            f"{name} bg {bg} is too close to white to be a visible "
            f"row signal"
        )
