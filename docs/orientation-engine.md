# Orientation engine

`OrientationOptimizer` takes a mesh, a material, load cases, constraints and a
set of weights, and returns ranked orientations with a full score breakdown
and a justification for each. This page describes the search; the formulas
behind the individual scores are in [physics.md](physics.md).

## What is actually searched

An orientation has three degrees of freedom. The physics only depends on two
of them:

* **Build direction `d`** (two degrees of freedom). Overhangs, layer
  alignment, bed contact, part height, warping span, anisotropy — all of it
  follows from `d`.
* **Spin about the build axis** (one degree of freedom). It changes nothing
  except the shape of the footprint on the plate.

So the engine searches the unit sphere of build directions and solves the spin
exactly, as the minimum-area bounding rectangle of the projected convex hull
(rotating calipers — the optimum is always flush with a hull edge, so it is
exact, not sampled). That criterion is the one that matters in practice: the
smallest footprint, which also decides whether the part fits the build volume.

A 15° grid over directions is roughly 250 candidates. A 15° grid over Euler
triples would be about 14 000, and would not find anything the direction grid
misses.

## Three stages

| Stage | Step | Candidates |
| --- | --- | --- |
| 1 · coarse | 15° over the sphere (configurable) | sphere grid + the six axis-aligned directions + one per large flat facet |
| 2 · refine | 5° inside a cone of the coarse step around the best 8 | grid inside the cone |
| 3 · fine | 1° inside a cone of the refine step around the best 3 | optional, on by default |

The sphere grid scales its azimuth count with `sin(polar angle)`, so samples
stay roughly equidistant instead of bunching at the poles. A test asserts that
no direction on the sphere is further than the step from a grid point.

**Flat-facet candidates.** A planar facet with outward normal `n` lies flat on
the plate when `d = −n`. These are the orientations a human tries first and a
sampled grid can miss them by several degrees, so they are injected
explicitly.

Candidates already evaluated are dropped before each refinement stage, and
near-duplicates are collapsed, so the stages do not re-score the same
direction.

## Parallelism

Candidate evaluation is spread over a thread pool (`PEO_OPTIMIZER_WORKERS`,
default 4). The work is numpy array arithmetic over precomputed face arrays,
which releases the GIL. The evaluation itself is pure — it only reads shared,
immutable arrays — so the result does not depend on the worker count, and a
regression test asserts that two runs produce identical scores.

## Scoring

Each candidate carries eight scores in 0…100 and the weighted total:

| Component | Default weight | Absolute or relative |
| --- | --- | --- |
| Mechanical | 35 % | absolute |
| Layer direction | 25 % | absolute |
| Support | 15 % | absolute |
| Overhang | 10 % | absolute |
| Bed stability | 5 % | absolute |
| Warping | 5 % | absolute |
| Material usage | 5 % | absolute |
| Print time | 0 % | relative to the evaluated set |

These are the documented defaults and they exist in exactly one place in the
code (`OptimizationWeights`). Weights are normalised before use, so only
ratios matter, and every weight is settable per request.

The print-time score is the one relative quantity: it is `100 × fastest / this`
over the candidates evaluated in the run. It is labelled as such in the report.

### Mechanical versus layer score

Two different questions, deliberately kept apart:

* **Layer direction score** — purely geometric. `100 × (1 − (ŝ · d)²)` for the
  dominant principal stress direction `ŝ`: the share of the dominant stress
  that acts inside the layer plane. It answers *is the load in the layer
  plane*.
* **Mechanical score** — strength-based and geometry-aware.
  `100 × u_bulk / u`, the fraction of the ideal isotropic capability the
  orientation retains under the weak-plane failure criterion, evaluated at the
  most highly utilised of the section stress states. It answers *does the part
  hold*.

The mechanical score has a hard lower bound at the material's
`layer_adhesion_factor`, which makes it easy to sanity-check: a PETG part
loaded straight across the layers scores 70.

### Multiple load cases

Each case carries a weight, normalised across cases. For each case the
governing stress state is the one with the highest utilisation; the case
scores come from that state, and the case scores are combined with the case
weights. The reported safety factor is the minimum over all cases.

## Presets

Presets are nothing but weights, defined once in `orientation/presets.py`, and
the UI shows the numbers:

| Preset | mech | layer | support | overhang | stability | warping | material | time |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Maximum strength | 0.40 | 0.30 | 0.10 | 0.07 | 0.05 | 0.04 | 0.02 | 0.02 |
| Balanced | 0.25 | 0.18 | 0.15 | 0.10 | 0.07 | 0.07 | 0.08 | 0.10 |
| Fast | 0.12 | 0.08 | 0.15 | 0.10 | 0.05 | 0.05 | 0.05 | 0.40 |
| Lightweight | 0.15 | 0.10 | 0.20 | 0.05 | 0.03 | 0.02 | 0.35 | 0.10 |

Moving any slider switches the preset to "custom"; nothing is hidden.

## Results

The reported candidates are spread: a candidate within 8° of one already
selected is skipped, because five variants of the same orientation one degree
apart tell the user nothing. Candidates that do not fit the build volume are
excluded from the ranking unless none fit, in which case they are ranked with
a warning.

## Without a load case

`printability_only` runs the same search with the mechanical and layer weights
redistributed over the printability criteria. The result carries a warning
saying it is a printability ranking, not a strength ranking, and the
confidence report scores the load-definition factor at zero.

Without it, the API refuses a request that has no load case or no fixed
surface, with an error naming which one is missing.

## Cost

Measured on this repository's benchmark (`pytest -m slow`), a 1 310 720
triangle model simplified to the 60 000-face analysis budget, 762 orientations
evaluated across all three stages:

| Stage | Time |
| --- | --- |
| upload, parse, repair, metrics | 17.3 s |
| mechanical model (12 section cuts on the full mesh) | 0.8 s |
| orientation search (15° / 5° / 1°) | 4.0 s |
| critical region detection | 7.6 s |

On the parts in `test-data/` (hundreds of triangles) the whole search takes
1–3 s.

## Extending it

* **A different objective** — add a score to `scoring/`, a key to `SCORE_KEYS`
  and a weight field. Nothing else knows the list.
* **A different search** — the stages are driven by `SearchConfig`; a gradient
  or CMA-ES refinement would replace stages 2 and 3 without touching scoring.
* **A real FEM backend** — see the seam described in
  [architecture.md](architecture.md). The search does not change at all.
