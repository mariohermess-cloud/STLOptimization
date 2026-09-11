# Physics and formulas

Every number the application produces comes from something on this page. Where
a quantity is an approximation or a heuristic it is labelled, and the constants
are given, so a result can be reproduced by hand.

**Units.** STL files carry no units; millimetres are assumed, which is the FDM
convention. Everything follows: mm, mm², mm³, N, N·mm, MPa (= N/mm²), g,
degrees.

**Frames.** The world frame has the build plate on the XY plane and the build
direction along +Z. An orientation is a rotation `R` applied to the part, so a
part point `p` lands at `R p`. The build direction **expressed in the part
frame** is `d = Rᵀ ẑ`. Every formula below uses `d`, which is why the mesh is
never actually rotated during the search.

Euler angles are reported as intrinsic X→Y→Z in degrees, i.e.
`R = Rx(rx) · Ry(ry) · Rz(rz)`. That is the same composition three.js uses for
`Euler(x, y, z, 'XYZ')`, so the exported orientation can be applied directly.

---

## 1. Geometry

| Quantity | How |
| --- | --- |
| Volume | Divergence theorem over the closed surface (`trimesh`). Reported as unreliable and clamped to the convex hull volume when the mesh is not watertight. |
| Surface area | Sum of triangle areas. |
| Centre of mass | Solid-body centroid of the closed mesh; falls back to the surface centroid if the mesh is not a volume. |
| Principal inertia | Eigen-decomposition of the inertia tensor of the solid. |
| PCA axes | Eigen-decomposition of the **area-weighted** covariance of face centroids. Weighting by area avoids the bias that raw vertices introduce in densely tessellated regions. |
| Solidity | `volume / convex hull volume`. |
| Mass | `volume × density`. **ESTIMATE**: a printed part with sparse infill weighs less. |

### Cross-section properties

A cut plane produces one or more closed outlines. Their integrals are
evaluated analytically from the polygon vertices (shoelace form), with
interior rings carrying the opposite orientation so holes subtract correctly:

```
A   = ½ Σ (xᵢ yᵢ₊₁ − xᵢ₊₁ yᵢ)
Q_y = ⅙ Σ (xᵢ + xᵢ₊₁)(xᵢ yᵢ₊₁ − xᵢ₊₁ yᵢ)
Q_x = ⅙ Σ (yᵢ + yᵢ₊₁)(xᵢ yᵢ₊₁ − xᵢ₊₁ yᵢ)
Ixx = 1/12 Σ (yᵢ² + yᵢ yᵢ₊₁ + yᵢ₊₁²)(xᵢ yᵢ₊₁ − xᵢ₊₁ yᵢ)
Iyy = 1/12 Σ (xᵢ² + xᵢ xᵢ₊₁ + xᵢ₊₁²)(xᵢ yᵢ₊₁ − xᵢ₊₁ yᵢ)
Ixy = 1/24 Σ (xᵢ yᵢ₊₁ + 2xᵢ yᵢ + 2xᵢ₊₁ yᵢ₊₁ + xᵢ₊₁ yᵢ)(xᵢ yᵢ₊₁ − xᵢ₊₁ yᵢ)
```

shifted to the centroid with the parallel axis theorem. For a neutral axis
along the in-plane direction `a` with in-plane normal `n = (−a_y, a_x)`:

```
I(a) = n_x² Iyy + 2 n_x n_y Ixy + n_y² Ixx
```

(`n_x` pairs with `Iyy`, not with `Ixx` — getting this backwards produces a
bending stress wrong by `(b/h)²`, which is why there is a regression test for
it against `W = b h² / 6`.)

`J = Ixx + Iyy` is used as the polar second moment. **APPROXIMATION**: this is
the exact torsion constant only for a circular section.

---

## 2. Mechanical analysis — `ApproximateMechanicalSolver`

**This is not a finite element analysis.** It is classical beam theory
evaluated on the real cross-sections of the model along the load path.

### Load path

For each load case:

* the reaction point is the area-weighted centroid of the selected fixed faces;
* the application point is the area-weighted centroid of the loaded faces, an
  explicit point, or — when neither is given — the material point furthest
  from the reaction, which is the worst-case lever arm;
* the load path axis `â` runs from the reaction to the application point;
* `section_samples = 12` cuts are taken perpendicular to `â`, spread over the
  path.

### Internal resultants at a section

With the section centroid `c` and lever `r = p_load − c`, the moment carried by
the cut is

```
M = r × F + T          [N·mm]     (torques are converted from N·m ×1000)
N = F · â              axial force        [N]
V = F − N â            transverse shear   [N]
M_t = M · â            torsion            [N·mm]
M_b = M − M_t â        bending            [N·mm]
```

### Stresses

```
σ_axial   = N / A
σ_bending = |M_b| · c_max / I(M_b)             c_max = extreme fibre distance
τ_torsion = |M_t| · r_max / J
τ_shear   = k · |V| / A                        k = 1.5
```

`k = 1.5` is exact for a rectangular section; **APPROXIMATION** for anything
else. `τ_torsion` is exact only for a circular section.

### Stress tensors

Two representative points per section, so the maxima are not stacked at a
point where they do not co-occur:

* **extreme fibre** — where bending is largest and transverse shear is zero:
  `S = (σ_axial + σ_bending)(â ⊗ â) + τ_torsion (â ⊗ t̂ + t̂ ⊗ â)`
* **neutral axis** — where transverse shear is largest and bending is zero:
  `S = τ_shear (â ⊗ v̂ + v̂ ⊗ â) + τ_torsion (â ⊗ t̂ + t̂ ⊗ â)`

with `t̂` tangential at the extreme fibre and `v̂` along the shear force.
These tensors are in part coordinates and **do not depend on the print
orientation**, which is what makes evaluating hundreds of orientations cheap:
only the direction `d` changes.

### Declared load type

The analysis always follows the actual force and torque vectors. The declared
type (tension, bending, …) is a label; when it disagrees with the vectors — a
"tension" load acting transversely, say — the mismatch is reported as a
warning rather than silently reinterpreted.

---

## 3. FDM anisotropy

The one material parameter carrying anisotropy:

```
σ⊥ = layer_adhesion_factor × σ∥
```

`σ∥` is the in-plane (XY) tensile strength, `σ⊥` the strength across the layer
interfaces. Shear strengths are derived, not measured:

```
τ∥ = σ∥ / √3                       (von Mises: τ_y = σ_y / √3)
τ⊥ = layer_adhesion_factor × τ∥
```

### Weak-plane (critical plane) criterion — used for scoring

Layer interfaces form a family of parallel planes with normal `d`. For the
stress state `S` the traction on such a plane is `t = S d`, which splits into

```
σ_n = t · d                       (only tension opens a weld)
τ_s = |t − σ_n d|
```

and the interface is checked with a quadratic interaction:

```
u_interlayer = sqrt( (max(σ_n, 0)/σ⊥)² + (τ_s/τ⊥)² )
```

The bulk material is checked independently with von Mises against `σ∥`:

```
u_bulk = σ_vm / σ∥
u      = max(u_interlayer, u_bulk)          utilisation; u ≥ 1 predicts failure
SF     = 1 / u                              safety factor
```

This is the standard "plane of weakness" approach used for bedded and
laminated materials, applied to layer interfaces. **APPROXIMATION**: it does
not capture void content, bead geometry, raster angle, or the influence of
print temperature on weld strength.

### Anisotropy penalty — the basis of the mechanical score

```
anisotropy_penalty = u_bulk / u   ∈ (0, 1]
```

Because `u ≥ u_bulk` by construction, the penalty is the fraction of the ideal
*isotropic* capability that this build direction retains. It isolates the
effect of the orientation from the effect of the load, which is exactly what
should be ranked. Its lower bound is the material's `layer_adhesion_factor`,
which is a useful sanity check: a PETG part loaded straight across the layers
scores `100 × 0.70 = 70`.

### Hankinson's formula — used for the reported allowable stress

For a uniaxial stress at an angle `θ` to the layer plane:

```
σ_allow(θ) = σ∥ σ⊥ / (σ∥ sin²θ + σ⊥ cos²θ)
```

returning `σ∥` in-plane and `σ⊥` across layers. Reported so the user can see
the allowable stress in the direction that actually carries the load. It is a
second, independent anisotropy model — the application does not mix the two:
scoring uses the weak-plane criterion, reporting uses Hankinson.

---

## 4. Overhangs and support

For a face with outward normal `n`, the inclination from the build plate is

```
phi = arccos(−n · d)
```

`phi = 0` is a horizontal down-facing surface, `phi = 90°` a vertical wall.
A face needs support when it faces down (`n · d < 0`), `phi < threshold`
(default 45°, options 45/50/55/60), and it is not resting on the plate.

Exact geometry so far. The support **volume** is an approximation:

```
support_volume ≈ Σ_f  A_f · |n_f · d| · h_f · 0.15          [mm³]
```

the projected area of each unsupported face swept down to the plate, times a
typical sparse support infill fraction of 0.15. **APPROXIMATION**: it ignores
that lower geometry may already carry the support.

Scores:

```
overhang_score = 100 · clamp(1 − Σ_f A_f · (threshold − phi_f)/threshold  /  A_total)
support_score  = 100 · exp(−2 · support_volume / part_volume)
material_score = 100 · V_part / (V_part + support_volume)
```

The overhang score weights each unsupported face by how far past the threshold
it is, so a nearly horizontal ceiling counts for more than a marginal one.

---

## 5. Bed contact and stability

A face rests on the plate when it faces down and its centroid is within the
bed tolerance (default 0.2 mm) of the lowest point. The footprint is the
convex hull of the vertices touching the plate.

Two independent physical criteria, averaged:

**Tipping.** The part tips about a footprint edge when the centre of mass
passes over it:

```
tipping_angle = atan( margin / com_height )
tipping_score = 100 · clamp(tipping_angle / 45°)        0 if the CoM is outside
```

where `margin` is the distance from the CoM projection to the footprint
boundary.

**Adhesion.** The first layer has to hold the part down:

```
A_ref          = 100 mm² · max(1, height / 50 mm)
adhesion_score = 100 · clamp(contact_area / A_ref)
```

`A_ref` is a **HEURISTIC** reference patch that grows with part height,
because a taller part applies more leverage to the same patch.

```
stability_score = 0.5 · tipping_score + 0.5 · adhesion_score
```

Averaging them is what stops the engine from automatically preferring the
largest flat face: a big flat footprint only wins the adhesion half.

`slenderness = height / sqrt(footprint_area)` is reported alongside.

---

## 6. Warping risk — heuristic

```
W = t_material · S · C · E                          clamped to [0, 1]

S = min(1, max_bed_span / 150 mm)          span factor
C = 0.5 + 0.5 · (contact_area / footprint_area)     solid first layer factor
E = 0.55 inside a heated enclosure, else 1.0

level: W < 0.25 LOW,  W < 0.55 MEDIUM,  else HIGH
warping_score = 100 · (1 − W)
```

`t_material` is the material warping index, a documented heuristic ordering
(0 = no warping observed in practice, 1 = severe), not a measured property.
When it is unknown a neutral 0.4 is assumed and the fact is reported.

**HEURISTIC.** There is no thermal simulation behind this. It orders
orientations by risk; it does not predict whether a part will lift.

---

## 7. Print time — approximation

```
T = V_extruded / (flow × 0.60) + layers × 1.5 s
V_extruded = V_part × 0.45 + support_volume
layers     = height / layer_height
```

`flow` is the material's maximum volumetric flow; 0.60 is the fraction a real
print sustains; 0.45 is a nominal material fraction for walls, skins and
sparse infill (print time is estimated before the settings are chosen);
1.5 s/layer covers layer change, travel and acceleration.

```
print_time_score = 100 × (fastest time in the evaluated set) / (this time)
```

The time score is therefore **relative to the candidates evaluated in this
run**; every other score is absolute.

**APPROXIMATION.** This is a comparative estimate for ranking orientations. It
is not a slicer and will not match a sliced G-code time. Replacing it with a
real slicer result is the highest-value V2 item.

---

## 8. Footprint and build volume

The spin about the build axis is not searched. It is solved exactly per
candidate: the minimum-area bounding rectangle of the convex hull of the
projected part, found by rotating calipers (the optimum is always flush with a
hull edge, so only the hull edge directions have to be tested). The part fits
when the sorted rectangle sides fit inside the sorted plate sides and the
height fits the build volume.

---

## 9. Overall score

```
overall = Σ_k  weight_k × score_k          weights normalised to sum to 1
```

with the default weights from `docs/orientation-engine.md`. Every component,
its weight and its contribution are reported, so the total can be recomputed
by hand. When no load case is defined, the mechanical and layer weights are
redistributed over the printability criteria and the fact is reported as a
warning.

---

## 10. Wall versus infill

Not a rule of thumb. The critical section outline is offset inwards by the
wall thickness `n × line_width`, and the second moment of area of the
remaining core is subtracted from that of the full section:

```
shell_inertia_fraction = (I_full − I_core) / I_full
```

with the core inertia shifted to the full-section centroid first. The wall
count is raised until this reaches the target for the chosen priority
(0.75 for maximum strength, 0.55 balanced, 0.35 fast), or the walls consume
the section. Infill is then set from what the **core** still has to carry:

```
infill = base + 0.45 × (1 − shell_inertia_fraction) × min(1, 2 × utilisation)
```

clamped to the range for the priority. The consequence is the intended one:
for a bending-dominated section, walls are added and infill stays low, and the
recommendation says so with the measured fraction.

---

## 11. Confidence

A weighted average of factors that measure the *input*, not the software:

| Factor | Weight | Driven by |
| --- | --- | --- |
| Mesh quality | 0.20 | watertight, winding, degenerate faces, body count |
| Geometric resolution | 0.10 | whether the analysis mesh was simplified |
| Material data | 0.20 | the per-property confidence levels actually used |
| Load and fixture definition | 0.20 | loads present, fixture selected, application region selected |
| Mechanical method | 0.15 | fixed at 45 for the approximate solver, 80 for a real FEM backend |
| Ranking margin | 0.15 | the gap between the best and the runner-up orientation |

The mechanical-method ceiling is deliberate: no amount of clean input turns
beam formulas into a validated stress analysis.

---

## 12. What is not modelled

* Stress concentrations at holes, fillets and sharp transitions.
* Fixture compliance, bolt preload, contact and friction.
* Buckling, creep, fatigue, impact and temperature-dependent properties.
* Residual stress from printing, and its interaction with applied load.
* Void content and bead geometry inside a printed wall.
* The actual thermal history of a layer interface, which is what really sets
  layer adhesion.

> This tool provides engineering-oriented estimates and FDM print optimisation
> guidance. It is not a certified structural analysis system.
