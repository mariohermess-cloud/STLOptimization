# Portable build

A self-contained bundle: no Python, no Node, no Docker, no installation.
Unpack the folder, run the executable, the browser opens.

## Using it

1. Download the bundle for your platform from the **Portable build** workflow
   (Actions → *Portable build* → the run → Artifacts), or from a release.
2. Unpack it and keep the folder together — the executable needs the
   `_internal` directory beside it.
3. Run `print-engineering-optimizer` (`.exe` on Windows). A console window
   opens showing the URL, and the browser follows a moment later.
4. Close the console window, or press Ctrl+C, to quit.

```
print-engineering-optimizer [--port 8765] [--host 127.0.0.1] [--no-browser]
```

It binds to loopback only by default. `--host 0.0.0.0` makes it reachable
from a tablet or phone on the same network — **the application has no
authentication**, so only on a network you trust.

Uploads go to a per-user temporary directory (printed at startup), not into
the bundle: the bundle directory is read-only on macOS and would be the wrong
place anyway. They are cleaned up by the TTL and by the operating system.

## Operating system warnings on first run

The binaries are **not code signed**, because signing needs a paid Apple
Developer account and a Windows code-signing certificate. Both systems will
say so, and it is not a sign that anything is wrong:

* **Windows** — SmartScreen shows "Windows protected your PC". *More info →
  Run anyway*.
* **macOS** — Gatekeeper refuses an unsigned, unnotarised binary. Either
  right-click the executable → *Open* → *Open*, or clear the quarantine flag:

  ```bash
  xattr -dr com.apple.quarantine print-engineering-optimizer
  ```

If that is unacceptable in your environment, build it yourself with the
recipe below — a locally built bundle carries no quarantine flag.

## Why one folder and not one file

PyInstaller can produce a single executable, and it is tempting. It also
unpacks the whole ~200 MB payload into a temporary directory on **every**
start, which makes launching slow and reliably upsets antivirus software.
One folder starts immediately.

## Why not Electron or a native window

The value of this application is the Python engine — trimesh, scipy, numpy.
Bundling a second runtime purely to host a browser view would roughly double
the download for a window frame. The system browser is a perfectly good
window, and the UI was already built to run in one.

## Size

About 250 MB unpacked, dominated by scipy, numpy and trimesh. The spec
excludes matplotlib, tkinter, IPython, Qt and the notebook stack, which
scipy and trimesh only touch in optional code paths; that alone saves
roughly a third. Compressing the download much further would mean giving up
the numerical stack, which is the application.

## Building it yourself

```bash
cd frontend && npm ci && npm run build && cd ..
python3.12 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt pyinstaller
.venv/bin/pyinstaller desktop/portable.spec --noconfirm
# → dist/print-engineering-optimizer/
```

PyInstaller only ever targets the platform it runs on: a Linux host produces
a Linux bundle. That is exactly why `.github/workflows/portable.yml` exists —
it runs the same build on `ubuntu-latest`, `windows-latest` and
`macos-latest`, and each job then **starts the binary it just built** and
checks that the API answers and the material database loaded. A bundle that
builds but cannot start is the characteristic PyInstaller failure, and that
check is what catches it before a user does.

## How it works

* `desktop/launcher.py` is the entry point. It locates the bundled resources
  (`sys._MEIPASS` when frozen, the repository root otherwise), picks a free
  port — falling back to an OS-assigned one if the preferred port is taken —
  points `PEO_STATIC_DIR` at the bundled web UI, starts uvicorn and opens the
  browser once `/health` answers.
* `backend/app/static.py` serves that bundle from the API process, with a
  single-page-app fallback for client-side routes and an explicit containment
  check so a path cannot escape the bundle directory. API prefixes are never
  answered with the SPA shell, so a mistyped endpoint still returns a proper
  404.
* Because one process serves both, the frontend's same-origin `/api` calls
  work unchanged — the same property that makes the Codespaces port forward
  work without configuration.
* `desktop/portable.spec` collects the parts PyInstaller's import analysis
  cannot see: uvicorn's string-resolved implementation classes, trimesh's
  lazily imported loaders, the compiled extensions of scipy, shapely, rtree,
  manifold3d and fast-simplification, and the material and printer JSON
  databases, which are read by path at runtime.

## Limitations

Same engine, same limitations as every other way of running this — it is the
identical code. Specific to this packaging:

| Limitation | Detail |
| --- | --- |
| Unsigned binaries | SmartScreen and Gatekeeper will warn; see above |
| ~250 MB | The numerical stack; not meaningfully reducible |
| No auto-update | Download a newer bundle to update |
| Single user | One process, in-memory model store, uploads lost on quit |
| Per-platform builds | A Linux build cannot produce a Windows binary |
