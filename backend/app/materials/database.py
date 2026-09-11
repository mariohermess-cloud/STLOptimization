"""Material database access and the derived quantities the solver needs."""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

from app.core.errors import MaterialNotFoundError
from app.schemas import (
    Confidence,
    CustomMaterial,
    Material,
    MaterialProperties,
    MaterialSelection,
    PrintDefaults,
    PropertyValue,
)

DATA_FILE = Path(__file__).parent / "data" / "materials.json"

#: Ratio of shear yield to tensile yield for an isotropic ductile solid.
#: Derived from the von Mises criterion (tau_y = sigma_y / sqrt(3)), not a
#: measured FDM property. Used only when a material has no measured shear data.
VON_MISES_SHEAR_RATIO = 1.0 / math.sqrt(3.0)


@lru_cache(maxsize=1)
def _raw() -> dict:
    with DATA_FILE.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=1)
def all_materials() -> list[Material]:
    data = _raw()
    materials: list[Material] = []
    for entry in data["materials"]:
        materials.append(
            Material(
                id=entry["id"],
                name=entry["name"],
                family=entry.get("family", "other"),
                fiber_filled=entry.get("fiber_filled", False),
                requires_enclosure=entry.get("requires_enclosure", False),
                properties=MaterialProperties(**entry["properties"]),
                print_defaults=PrintDefaults(**entry.get("print_defaults", {})),
                notes=entry.get("notes", []),
            )
        )
    return materials


def database_disclaimer() -> str:
    return _raw()["disclaimer"]


def get_material(material_id: str) -> Material:
    for material in all_materials():
        if material.id == material_id:
            return material.model_copy(deep=True)
    raise MaterialNotFoundError(f"Material '{material_id}' is not in the material database.")


def material_from_custom(custom: CustomMaterial) -> Material:
    """Build a full material record from user supplied values.

    Unspecified secondary properties are inherited from the chosen base
    material when one is given, otherwise they stay unknown and the confidence
    report reflects that.
    """
    base = None
    if custom.base_material_id:
        try:
            base = get_material(custom.base_material_id)
        except MaterialNotFoundError:
            base = None

    properties = MaterialProperties(
        density=PropertyValue(
            value=custom.density_g_cm3, confidence=Confidence.medium, source="User supplied"
        ),
        young_modulus=PropertyValue(
            value=custom.young_modulus_mpa, confidence=Confidence.medium, source="User supplied"
        ),
        tensile_strength=PropertyValue(
            value=custom.tensile_strength_mpa, confidence=Confidence.medium, source="User supplied"
        ),
        layer_adhesion_factor=PropertyValue(
            value=custom.layer_adhesion_factor, confidence=Confidence.medium, source="User supplied"
        ),
    )
    if custom.thermal_limit_c is not None:
        properties.thermal_limit = PropertyValue(
            value=custom.thermal_limit_c, confidence=Confidence.medium, source="User supplied"
        )
    elif base:
        properties.thermal_limit = base.properties.thermal_limit

    if custom.warp_tendency is not None:
        properties.warp_tendency = PropertyValue(
            value=custom.warp_tendency, confidence=Confidence.low, source="User supplied (heuristic index)"
        )
    elif base:
        properties.warp_tendency = base.properties.warp_tendency

    if custom.max_volumetric_flow_mm3_s is not None:
        properties.max_volumetric_flow = PropertyValue(
            value=custom.max_volumetric_flow_mm3_s, confidence=Confidence.medium, source="User supplied"
        )
    elif base:
        properties.max_volumetric_flow = base.properties.max_volumetric_flow

    return Material(
        id=f"custom:{custom.name}",
        name=custom.name,
        family=base.family if base else "custom",
        fiber_filled=base.fiber_filled if base else False,
        requires_enclosure=base.requires_enclosure if base else False,
        is_custom=True,
        properties=properties,
        print_defaults=base.print_defaults if base else PrintDefaults(),
        notes=["User defined material. Property provenance is the user, not a data sheet."],
    )


def resolve(selection: MaterialSelection) -> Material:
    if selection.custom is not None:
        return material_from_custom(selection.custom)
    assert selection.material_id is not None  # guaranteed by the schema validator
    return get_material(selection.material_id)


# --------------------------------------------------------------------------
# derived engineering quantities
# --------------------------------------------------------------------------
def in_plane_tensile_strength(material: Material) -> float:
    """sigma_parallel - tensile strength in the layer plane (XY), MPa."""
    value = material.properties.tensile_strength.value
    if value is None or value <= 0:
        raise MaterialNotFoundError(
            f"Material '{material.name}' has no tensile strength; a mechanical "
            "analysis cannot be run with it."
        )
    return float(value)


def interlayer_tensile_strength(material: Material) -> float:
    """sigma_perpendicular - tensile strength across the layer interfaces, MPa.

    ``sigma_perp = layer_adhesion_factor * sigma_parallel``. This is the single
    parameter that carries FDM anisotropy through the whole application.
    """
    factor = material.properties.layer_adhesion_factor.value
    if factor is None:
        factor = 0.5  # documented fallback, flagged by the confidence report
    return in_plane_tensile_strength(material) * float(factor)


def in_plane_shear_strength(material: Material) -> float:
    """APPROXIMATION: von Mises derived shear strength in the layer plane."""
    return in_plane_tensile_strength(material) * VON_MISES_SHEAR_RATIO


def interlayer_shear_strength(material: Material) -> float:
    """APPROXIMATION: shear strength on the layer interface plane.

    Interlayer shear is limited by the same weld quality as interlayer tension,
    so the same adhesion factor is applied.
    """
    factor = material.properties.layer_adhesion_factor.value
    if factor is None:
        factor = 0.5
    return in_plane_shear_strength(material) * float(factor)


def density_g_mm3(material: Material) -> float:
    value = material.properties.density.value
    if value is None or value <= 0:
        return 0.0
    return float(value) / 1000.0  # g/cm^3 -> g/mm^3


def mass_estimate_g(material: Material, volume_mm3: float, infill_density: float = 1.0) -> float:
    """Mass estimate. ESTIMATE: solid volume times density.

    With ``infill_density < 1`` the value is a crude lower bound - it ignores
    that walls, top and bottom layers are solid.
    """
    return density_g_mm3(material) * volume_mm3 * max(0.0, min(1.0, infill_density))
