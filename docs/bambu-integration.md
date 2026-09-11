# Bambu Studio integration

Research state as of **September 2026**. Everything below is either quoted
from a source that is named, or derived from the Bambu Studio source code with
the derivation shown. Nothing here is invented, and the integration paths that
do **not** exist are stated as plainly as the ones that do.

The MVP **does not integrate with Bambu Studio**. It exports a JSON document
with the calculated values. This page records what an integration could
realistically do, so that V2 can be built without re-doing the research.

---

## 1. Summary

| Capability | Status | Notes |
| --- | --- | --- |
| Read/write Bambu Studio **preset JSON** | Possible, officially shipped format | Key names verified against the repository, see §3 |
| Slice headlessly via the **`bambu-studio` CLI** | Possible, documented by Bambu | Wiki page "Command Line Usage", see §2 |
| Produce a **3MF project** Bambu Studio can open | Possible in principle | 3MF is an open standard; the Bambu project extensions are not formally specified. See §4 |
| Public **REST API for slicing** | Does not exist | No Bambu-published slicing API |
| Public **plugin API** for Bambu Studio | Does not exist | The application has no documented extension point |
| Send a print job to a printer over the **local network** | Reverse engineered only, and gated since Jan 2025 | See §5 |
| **Bambu Cloud API** | Reverse engineered only, not supported | See §5 |

---

## 2. Command line interface (officially documented)

Source: the Bambu Lab wiki page
[Command Line Usage](https://github.com/bambulab/BambuStudio/wiki/Command-Line-Usage).

The options documented there:

```
--debug level                  logging severity 0-5
--load-filaments "a.json;b.json;..."
--load-settings  "machine.json;process.json"     (at most 1 machine + 1 process)
--outputdir dir
--arrange option               0 disable, 1 enable, other = auto
--orient
--scale factor
--slice plate_index            0 = all plates
--export-3mf filename.3mf
--export-settings settings.json
--export-slicedata directory
--load-slicedata directory
--uptodate
--info
--pipe pipename
--help, -h
```

Documented example:

```
bambu-studio --slice 0 --debug 2 --export-3mf output.3mf input.3mf
bambu-studio --curr-bed-type "Cool Plate" \
             --load-settings "machine.json;process.json" \
             --load-filaments "filament1.json" \
             --slice 2 --export-3mf output.3mf input.3mf
```

Documented limitations, quoted from the wiki:

* `--load-settings` supports "up to 1 machine setting and 1 process setting";
* the number of filaments passed to `--load-filaments` "should not exceed the
  filaments used in the 3mf";
* the JSON files must be **full config** files, not the abbreviated presets
  found in the resource directories.

**What this means for us.** A future integration writes a process JSON, hands
it to the CLI together with the model, and gets a sliced 3MF back. That is a
real, officially documented path.

**What it does not give us.** There is no documented CLI option to set the
object's orientation. `--orient` runs *Bambu Studio's own* auto-orientation
(see §6), which would overwrite ours. A recommended orientation therefore has
to be baked into the model or into the project file before slicing - the open
question in §4.

> Not verified by this project: the CLI has not been executed here. There is
> no Bambu Studio binary in this repository or in its container images, and
> the behaviour of specific versions (notably around external presets) is the
> subject of open upstream issues.

---

## 3. Preset JSON format (officially shipped)

Presets are plain JSON, split into **machine**, **filament** and **process**.
The keys below were read from the repository, not guessed:

* `resources/profiles/BBL/process/0.20mm Standard @BBL X1C.json` - a shipped
  process preset, which carries `"type": "process"`, `"name"`, `"inherits"`,
  `"from": "system"`, `"setting_id"`, `"instantiation"` and
  `"compatible_printers"`, and inherits its geometry settings.
* `resources/profiles/BBL/process/fdm_process_common.json` - the common base,
  which is where the settings this application computes actually live.

Verified key names and shipped defaults:

| Bambu key | Default | What this application computes |
| --- | --- | --- |
| `layer_height` | `"0.2"` | `layer_height` |
| `initial_layer_print_height` | `"0.2"` | `first_layer_height` |
| `wall_loops` | `"2"` | `wall_loops` |
| `outer_wall_line_width` | `"0.42"` | used as the line width in the wall analysis |
| `inner_wall_line_width` | `"0.45"` | - |
| `sparse_infill_density` | `"15%"` | `infill_density` (note: **percent string**) |
| `sparse_infill_pattern` | `"grid"` | `infill_pattern` |
| `top_shell_layers` | `"3"` | `top_layers` |
| `bottom_shell_layers` | `"3"` | `bottom_layers` |
| `top_shell_thickness` | `"0.8"` | implied by the skin thickness target |
| `enable_support` | `"0"` | `supports` |
| `support_type` | `"tree(auto)"` | `support_type` |
| `support_threshold_angle` | `"30"` | `support_angle` - **see the convention note below** |
| `support_style` | `"default"` | not computed |
| `brim_width` | `"5"` | `brim_width_mm` |
| `brim_type` | - | not computed; enum below |

Enum values, read from `src/libslic3r/PrintConfig.cpp`:

* `support_type`: `normal(auto)`, `tree(auto)`, `normal(manual)`, `tree(manual)`
* `support_style`: `default`, `grid`, `snug`, `tree_slim`, `tree_strong`,
  `tree_hybrid`, `tree_organic`
* `brim_type`: `no_brim`, `outer_only`, `inner_only`, `outer_and_inner`,
  `auto_brim`, `brim_ears`
* `sparse_infill_pattern`: `concentric`, `zig-zag`, `grid`, `line`, `cubic`,
  `triangles`, `tri-hexagon`, `gyroid`, `honeycomb`, `adaptivecubic`,
  `monotonic`, `monotonicline`, `alignedrectilinear`, `3dhoneycomb`,
  `hilbertcurve`, `archimedeanchords`, `octagramspiral`, `supportcubic`,
  `lightning`, `crosshatch`, `zigzag`, `crosszag`, `lockedzag`, `2dlattice`,
  `ironingarchimedeanspiral`

The values this application emits for `infill_pattern` (`grid`, `gyroid`,
`lightning`) are all in that enum, so they map across directly.

**Note on value types.** Bambu preset values are *strings*, densities carry a
`%` sign, and many process keys are *arrays* (one entry per extruder variant).
Our export uses plain JSON numbers. A converter has to do that translation
explicitly; this application does not silently pretend its JSON is a Bambu
preset.

### The support threshold angle convention

This one matters and is easy to get backwards, so the derivation is recorded.

`src/libslic3r/PrintConfig.hpp` comments the option as "Overhang angle
threshold". `src/libslic3r/Orient.cpp` converts it to the internal threshold:

```cpp
orienter.params.overhang_angle = obj->config.opt_int("support_threshold_angle");
orienter.params.ASCENT = cos(PI - orienter.params.overhang_angle * PI / 180);
```

and `src/libslic3r/Orient.hpp` gives the default `ASCENT = -0.86602540378f`,
which is exactly `-cos(30°)` and matches the shipped default of `30`.

For a down-facing face with outward normal `n` and build direction `d`, the
inclination of the face from the build plate is `phi`, with `n · d = -cos(phi)`
(a flat ceiling gives `n · d = -1, phi = 0`; a vertical wall gives
`n · d = 0, phi = 90°`). With the threshold `ASCENT = -cos(theta)`, the
overhang test `n · d < ASCENT` reduces to `phi < theta`.

So **`support_threshold_angle` is measured from the build plate, the same
convention this application uses for `overhang_threshold_deg`** - but Bambu's
shipped default is `30`, while this application defaults to `45`, which is
more conservative (it asks for support on more surfaces).

> Confidence: derived from source, not from an official statement of the
> convention. The Bambu wiki page describing the parameter was not reachable
> from this environment. Verify against your installed version before relying
> on a numeric conversion. This is why the exporter writes its own
> unambiguous `support_angle` field with its convention documented, rather
> than silently writing a Bambu key.

---

## 4. 3MF project files

3MF itself is an open, published standard (a ZIP container with XML part
descriptions). Bambu Studio reads and writes 3MF, and its project files carry
additional Bambu-specific parts (plate layout, per-object settings, the
project configuration). Those extensions are **not formally specified by
Bambu**; they can be read from the source, but they are not a stable contract.

Consequences for a future integration:

* Writing a plain 3MF mesh is safe and standard.
* Writing a **Bambu project** 3MF with a baked orientation and per-object
  settings means writing against an unspecified, version-dependent layout.
  That is reverse engineering, and it should be labelled as such.
* The lowest-risk path is therefore: export the mesh already rotated into the
  recommended orientation as a plain mesh file, and supply the print settings
  as a process preset JSON for `--load-settings`. That keeps every part of the
  exchange on documented ground.

---

## 5. Talking to the printer

* **No public, officially documented API** exists for submitting a print job
  to a Bambu Lab printer.
* Community projects communicate with the printer over **MQTT on port 8883**,
  with topics `device/{SERIAL}/report` and `device/{SERIAL}/request`. This is
  reverse engineered.
* Since a **January 2025 firmware change**, LAN control requires
  authenticated, signed requests (RSA-SHA256 with an X.509 certificate);
  unsigned commands are rejected. This broke existing third-party tooling.
* The **Bambu Cloud API** used by the official clients is likewise
  reverse engineered by the community and is not a supported integration
  surface.

**Position taken by this project:** printer communication is out of scope.
A print-submission feature would rest on an unsupported, deliberately gated
interface that can break with any firmware release. If it is ever added it
belongs behind an explicit opt-in, clearly labelled as unsupported.

---

## 6. Bambu Studio already has an auto-orientation feature

`src/libslic3r/Orient.cpp` implements an auto-orientation pass (exposed as
`--orient` on the CLI and as "Auto orient" in the GUI). It is a
**printability** optimiser in the Tweaker lineage: it scores orientations by
overhang area, bed contact and part height, using `support_threshold_angle` as
the overhang criterion.

It does not model loads, materials or anisotropy - there is no load case to
model, because a slicer has no idea what the part is for. That is exactly the
gap this application fills, and it is the reason a future integration must
**not** pass `--orient`: it would discard the mechanical result.

---

## 7. Recommended V2 integration path

1. Export the model rotated into the chosen orientation as a plain mesh
   (STL or plain 3MF). Standard formats only.
2. Emit a Bambu **process preset JSON** from the recommended settings, using
   the verified key names in §3, with values converted to Bambu's string and
   percentage conventions, and `support_threshold_angle` converted only after
   the convention has been confirmed against the target version.
3. Invoke the CLI:
   `bambu-studio --load-settings "machine.json;process.json" --load-filaments "filament.json" --slice 0 --export-3mf out.3mf oriented.3mf`
   without `--orient` and without `--arrange`.
4. Treat the resulting 3MF and the slicing data as the authoritative print
   time and material figures, replacing this application's estimates.

Step 4 is the most valuable part: it would turn the labelled-approximate print
time and material estimates into measured values from a real slicer.

---

## Sources

* [Bambu Studio wiki - Command Line Usage](https://github.com/bambulab/BambuStudio/wiki/Command-Line-Usage)
* [`resources/profiles/BBL/process/fdm_process_common.json`](https://github.com/bambulab/BambuStudio/blob/master/resources/profiles/BBL/process/fdm_process_common.json)
* [`resources/profiles/BBL/process/0.20mm Standard @BBL X1C.json`](https://github.com/bambulab/BambuStudio/blob/master/resources/profiles/BBL/process)
* [`src/libslic3r/PrintConfig.cpp`, `PrintConfig.hpp`, `Orient.cpp`, `Orient.hpp`](https://github.com/bambulab/BambuStudio/tree/master/src/libslic3r)
* [Bambu Lab reverse-engineering knowledge base (community)](https://contentnation.net/en/grumpydevelop/bl-knowledge)
* [`bambulabs_api` MQTT client documentation (community)](https://bambutools.github.io/bambulabs_api/api/mqtt_client.html)
