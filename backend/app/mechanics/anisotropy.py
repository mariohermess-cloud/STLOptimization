"""FDM anisotropy: how layer direction changes what a part can carry.

An FDM part is not isotropic. Material deposited inside a layer is a
continuous extrudate; material *between* layers is a weld whose strength
depends on the thermal history of the interface. The whole application carries
this through one material parameter,
``layer_adhesion_factor = sigma_perpendicular / sigma_parallel``.

Two models are used, and the application never mixes them up:

**1. Weak-plane (critical plane) criterion - used for scoring.**
The layer interfaces form a family of parallel planes with normal ``d`` (the
build direction, expressed in the part frame). For the stress tensor ``S`` at
a point, the traction on that plane is ``t = S d``. Splitting it into the
component normal to the interface and the component in the interface,

    sigma_n = t . d                      (only tension opens a weld)
    tau_s   = |t - sigma_n d|

failure of the interface is checked with a quadratic interaction

    u_interlayer = sqrt( (max(sigma_n,0)/sigma_perp)^2 + (tau_s/tau_perp)^2 )

The bulk material is checked independently with von Mises against the in-plane
strength, and the utilisation of the point is the larger of the two. This is
the standard "plane of weakness" approach used for laminated and bedded
materials, applied to layer interfaces.

**2. Hankinson's formula - used for the reported allowable stress.**
For a uniaxial stress at an angle ``theta`` to the layer plane,

    sigma_allow(theta) = sigma_par * sigma_perp
                         / (sigma_par * sin^2(theta) + sigma_perp * cos^2(theta))

which returns ``sigma_par`` in-plane and ``sigma_perp`` across layers. It is
reported so a user can see the allowable stress in the direction that actually
carries the load.

APPROXIMATION: both models are engineering approximations of a transversely
isotropic solid. They do not capture void content, bead geometry, raster
angle, or the influence of print temperature on weld strength.
"""

from __future__ import annotations

import numpy as np


def hankinson_allowable(sigma_par: float, sigma_perp: float, cos_to_build: float) -> float:
    """Allowable uniaxial stress for a direction at ``arccos(cos_to_build)``
    from the build direction.

    ``cos_to_build`` is ``|s . d|`` for the unit stress direction ``s``; it is
    ``sin(theta)`` with ``theta`` measured from the layer plane.
    """
    c = float(min(1.0, max(0.0, abs(cos_to_build))))
    sin2 = c * c
    cos2 = 1.0 - sin2
    denominator = sigma_par * sin2 + sigma_perp * cos2
    if denominator <= 1e-12:
        return sigma_par
    return float(sigma_par * sigma_perp / denominator)


def von_mises(stress: np.ndarray) -> float:
    """Von Mises equivalent stress of a symmetric 3x3 stress tensor."""
    s = np.asarray(stress, dtype=float)
    deviatoric = s - np.trace(s) / 3.0 * np.eye(3)
    return float(np.sqrt(1.5 * np.sum(deviatoric * deviatoric)))


def interface_traction(stress: np.ndarray, build_dir: np.ndarray) -> tuple[float, float]:
    """Normal and shear stress on the layer interface plane.

    ``build_dir`` is the unit build direction in the part frame, which is also
    the normal of every layer interface.
    """
    s = np.asarray(stress, dtype=float)
    d = np.asarray(build_dir, dtype=float)
    d = d / max(np.linalg.norm(d), 1e-12)
    traction = s @ d
    sigma_n = float(traction @ d)
    shear_vector = traction - sigma_n * d
    return sigma_n, float(np.linalg.norm(shear_vector))


def utilization(
    stress: np.ndarray,
    build_dir: np.ndarray,
    *,
    sigma_par: float,
    sigma_perp: float,
    tau_par: float,
    tau_perp: float,
) -> dict[str, float]:
    """Utilisation of a stress state for a given build direction.

    ``utilization >= 1`` means the criterion predicts failure.
    ``anisotropy_penalty`` is ``u_isotropic / u_anisotropic`` in ``(0, 1]``:
    the fraction of the ideal isotropic capability that this build direction
    retains. It is the quantity the mechanical score is built on, because it
    isolates the effect of the orientation from the effect of the load.
    """
    sigma_n, tau_s = interface_traction(stress, build_dir)
    tensile_n = max(sigma_n, 0.0)

    u_interlayer = float(
        np.sqrt((tensile_n / max(sigma_perp, 1e-9)) ** 2 + (tau_s / max(tau_perp, 1e-9)) ** 2)
    )
    u_bulk = von_mises(stress) / max(sigma_par, 1e-9)
    u_total = max(u_interlayer, u_bulk)

    if u_total <= 1e-12:
        penalty = 1.0
    else:
        penalty = float(min(1.0, u_bulk / u_total))

    return {
        "utilization": u_total,
        "utilization_interlayer": u_interlayer,
        "utilization_bulk": u_bulk,
        "interface_normal_stress_mpa": sigma_n,
        "interface_shear_stress_mpa": tau_s,
        "anisotropy_penalty": penalty,
        "interlayer_governs": u_interlayer > u_bulk,
        "tau_par": tau_par,
    }


def dominant_stress_direction(stress: np.ndarray) -> tuple[np.ndarray, float]:
    """Principal direction carrying the largest absolute principal stress."""
    s = np.asarray(stress, dtype=float)
    values, vectors = np.linalg.eigh(s)
    index = int(np.argmax(np.abs(values)))
    return vectors[:, index], float(values[index])


def in_plane_fraction(stress: np.ndarray, build_dir: np.ndarray) -> float:
    """Fraction of the dominant principal stress that acts in the layer plane.

    ``1 - (s . d)^2`` for the dominant principal direction ``s``. This is the
    pure geometric layer-alignment measure used by the *layer score*, kept
    separate from the strength-based mechanical score on purpose: one answers
    "is the load in the layer plane", the other answers "does the part hold".
    """
    direction, magnitude = dominant_stress_direction(stress)
    if abs(magnitude) < 1e-12:
        return 1.0
    d = np.asarray(build_dir, dtype=float)
    d = d / max(np.linalg.norm(d), 1e-12)
    alignment = float(np.dot(direction, d))
    return float(1.0 - alignment * alignment)
