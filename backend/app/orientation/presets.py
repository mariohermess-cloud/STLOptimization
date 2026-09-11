"""User priority presets.

Presets are nothing more than a set of optimisation weights. They are defined
here once; the UI shows the numbers and lets the user override any of them, so
a preset is a starting point rather than a hidden mode.
"""

from __future__ import annotations

from app.schemas import OptimizationWeights, Preset

PRESET_WEIGHTS: dict[Preset, OptimizationWeights] = {
    # Mechanical performance, layer orientation and wall strategy dominate.
    Preset.max_strength: OptimizationWeights(
        mechanical=0.40,
        layer=0.30,
        support=0.10,
        overhang=0.07,
        stability=0.05,
        warping=0.04,
        material=0.02,
        print_time=0.02,
    ),
    # Strength, time and material carry comparable weight.
    Preset.balanced: OptimizationWeights(
        mechanical=0.25,
        layer=0.18,
        support=0.15,
        overhang=0.10,
        stability=0.07,
        warping=0.07,
        material=0.08,
        print_time=0.10,
    ),
    # Print time first; strength only enough to avoid an obviously bad result.
    Preset.fast: OptimizationWeights(
        mechanical=0.12,
        layer=0.08,
        support=0.15,
        overhang=0.10,
        stability=0.05,
        warping=0.05,
        material=0.05,
        print_time=0.40,
    ),
    # Minimum material, which in practice means minimum support.
    Preset.lightweight: OptimizationWeights(
        mechanical=0.15,
        layer=0.10,
        support=0.20,
        overhang=0.05,
        stability=0.03,
        warping=0.02,
        material=0.35,
        print_time=0.10,
    ),
}

PRESET_LABELS = {
    Preset.max_strength: "Maximum strength",
    Preset.balanced: "Balanced",
    Preset.fast: "Fast",
    Preset.lightweight: "Lightweight",
    Preset.custom: "Custom",
}


def weights_for(preset: Preset, custom: OptimizationWeights) -> OptimizationWeights:
    """Weights actually used for a request.

    ``Preset.custom`` keeps whatever the client sent, which defaults to the
    documented default weights of :class:`OptimizationWeights`.
    """
    if preset is Preset.custom:
        return custom
    return PRESET_WEIGHTS[preset].model_copy()
