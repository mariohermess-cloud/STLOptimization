# Material database

## What these numbers are, and what they are not

The values in `backend/app/materials/data/materials.json` are **representative
figures for FDM filaments, compiled from public vendor technical data sheets
and polymer literature**. They are not measurements of any particular spool,
and filament brands vary widely — two spools both labelled "PETG" can differ
by 30 % in printed tensile strength.

Because of that, every property carries four things:

```json
"tensile_strength": {
  "value": 48, "min": 40, "max": 55,
  "confidence": "medium",
  "source": "Vendor TDS, XY printed tensile strength"
}
```

* `value` — the figure used by the calculation,
* `min` / `max` — the range the published figures span,
* `confidence` — `high`, `medium`, `low` or `unknown`,
* `source` — where it comes from, or an explicit `HEURISTIC` marker.

The confidence levels are not decoration: they feed the analysis confidence
score directly (weight 0.20), so a material whose properties are poorly known
lowers the reported confidence of the whole analysis, and the confidence panel
names which property was the weak one.

**Nothing here should be treated as a datasheet value for your filament.**
Verify against the technical data sheet of the spool you actually print, and
use the custom-material feature when you have measured values.

## Confidence, by property class

| Property | Typical confidence | Why |
| --- | --- | --- |
| `density` | high (medium for filled grades) | A bulk resin property, stable across brands. Filler content varies, which is why filled grades drop a level. |
| `young_modulus` | medium unfilled, low filled | Printed specimens differ from injection-moulded ones; filled grades vary enormously between brands. |
| `tensile_strength` | medium unfilled, low filled | Same, plus a strong dependence on print settings. |
| `thermal_limit` | high–medium for amorphous, low for semi-crystalline | For PLA/PETG/ABS/ASA/PC this is derived from the glass transition, which is well known. Polyamides have no sharp limit. |
| `max_volumetric_flow` | medium–low | A machine/hot-end property as much as a material one. |
| `layer_adhesion_factor` | **low, always** | See below. |
| `warp_tendency` | **low, always — a heuristic index, not a physical property** | See below. |

## The two heuristic parameters

### `layer_adhesion_factor`

The ratio of Z strength to XY strength, `σ⊥ / σ∥`. It is the single parameter
that carries FDM anisotropy through the whole application, and it is the least
certain number in the database.

It is **not** a material constant. It depends on nozzle temperature, chamber
temperature, cooling, layer height, extrusion width and part geometry. The
same filament can land anywhere from 0.3 to 0.9 depending on how it is
printed. The values here are mid-range figures from the published FDM
anisotropy literature, and every one is marked `confidence: low` with a
`HEURISTIC` source note.

The ordering encoded in the database, and the reasoning:

* **TPU highest (~0.85)** — elastomers bond well and their compliance
  redistributes stress.
* **PETG and PA high (~0.70)** — consistently reported as good-bonding; PETG
  cools slowly, polyamides are printed hot and in a chamber.
* **PC (~0.60)** — good when printed very hot in a closed chamber, poor
  otherwise.
* **PLA, ABS, ASA middling (~0.55)** — ABS and ASA are solvent-weldable and
  bond well *in a heated chamber*; that is why the X1C's enclosure matters.
* **Fibre-filled grades lowest (0.40–0.50)** — this is the important one, and
  it is counter-intuitive. Short fibres align with the extrusion direction, so
  the reinforcement acts in XY only. The Z direction still relies on the
  matrix. Filling therefore *raises* absolute XY strength and *lowers* the
  Z/XY ratio, which makes a fibre-filled part more sensitive to orientation,
  not less.

The application never claims this factor is exact. When it is low-confidence
(which is always, for the built-in materials), each orientation carries the
warning that the mechanical *ranking* is more reliable than the absolute
safety factor — the ranking depends mostly on the relative comparison, the
safety factor depends on the absolute value.

### `warp_tendency`

A dimensionless index from 0 (no warping observed in practice) to 1 (severe).
**It is not a physical property and there is no measurement behind it.** The
ordering follows mould shrinkage and processing temperature:

| Material | Index | Reasoning |
| --- | --- | --- |
| PC | 0.90 | Highest processing temperature and large thermal contraction |
| ABS | 0.85 | High mould shrinkage (~0.4–0.8 %) |
| ASA | 0.80 | Similar to ABS |
| PA | 0.75 | Semi-crystalline, high shrinkage |
| ASA-CF | 0.55 | Fibre filling measurably reduces shrinkage |
| PA-CF | 0.50 | Same |
| PETG | 0.25 | Low shrinkage; lifting is usually a first-layer adhesion problem |
| PETG-CF | 0.20 | Filled |
| PLA | 0.15 | Low shrinkage, low processing temperature |
| PLA-CF | 0.12 | Filled |
| TPU | 0.10 | Compliant; relieves internal stress instead of lifting |

It feeds only the warping heuristic in [physics.md §6](physics.md), which
produces a LOW/MEDIUM/HIGH risk label and a brim recommendation. It never
enters a strength calculation.

## Derived properties

Two values are **derived, not stored**, and their derivation is documented in
code:

```
τ∥ = σ∥ / √3        von Mises shear yield for an isotropic ductile solid
τ⊥ = layer_adhesion_factor × τ∥
```

Using the von Mises relation rather than inventing a shear strength is the
point: it is a documented assumption with a name, and it is flagged as an
approximation wherever it surfaces.

## Materials in the database

`PLA`, `PLA-CF`, `PETG`, `PETG-CF`, `ABS`, `ASA`, `ASA-CF`, `PA`, `PA-CF`,
`PC`, `TPU 95A`.

Notes attached to specific entries:

* **TPU** carries an explicit warning that a linear elastic analysis does not
  describe it. Its modulus is only meaningful at small strain, and it fails at
  very large elongation, so any utilisation reported for TPU is indicative
  only. The application shows the warning with the recommendation.
* **Polyamides** are hygroscopic. The published figures are dry-as-printed;
  moisture uptake lowers modulus and strength substantially, and the database
  says so in the source field rather than quietly using the dry number.
* **Fibre-filled grades** carry a hardened-nozzle warning in the settings
  recommendation.

## Custom materials

A user can define a material with four required properties — density, Young's
modulus, tensile strength and layer adhesion factor — plus optional thermal
limit, warping index and flow limit. Unspecified secondary properties can be
inherited from a chosen base material; anything still unknown stays unknown
and the confidence report reflects it.

Custom materials are stored in the browser's local storage and sent with each
request. They are never written to the server's database. Their provenance is
recorded as "User supplied", which is honest: the source is the user, not a
data sheet.

## Adding a material

Add an entry to `backend/app/materials/data/materials.json`. The test suite
enforces that:

* every property used by the solver has a value, a source and a confidence
  level;
* `min ≤ value ≤ max` where a range is given;
* `layer_adhesion_factor` and `warp_tendency` are marked `HEURISTIC` in their
  source string;
* the interlayer strength never exceeds the in-plane strength.

That last check is what stops an entry from accidentally claiming a part is
stronger across the layers than within them.

## Printer data is separate

Printer profiles live in `backend/app/printing/data/printers.json` and are
kept strictly separate: adding a machine never requires touching materials and
vice versa. The Bambu Lab X1 Carbon profile records the published build volume
(256 × 256 × 256 mm), the available nozzle diameters (0.2 / 0.4 / 0.6 / 0.8 mm),
the enclosed chamber and the heated bed limit, with the source noted in the
file.
