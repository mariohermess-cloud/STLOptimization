"""Entry point of the portable build.

One process, no installation: it serves both the API and the web UI, picks a
free port, opens the browser and stays up until Ctrl+C or the window is
closed.

Why this exists rather than a packaged Electron shell: the whole value of the
application is the Python engine (trimesh, scipy, numpy). Bundling a second
runtime just to host a browser view would double the download for a window
frame. The system browser is a perfectly good window, and the UI is already
built to run in one.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path


def bundle_root() -> Path:
    """Directory holding the bundled resources.

    Under PyInstaller that is the extraction directory (``sys._MEIPASS``);
    running from a source checkout it is the repository root.
    """
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        return Path(frozen)
    return Path(__file__).resolve().parents[1]


def free_port(preferred: int) -> int:
    """``preferred`` if it is free, otherwise a port the OS hands out.

    Binding to an already used port would fail at startup with a stack trace;
    a second copy of the app, or anything else on 8765, should not stop it.
    """
    for candidate in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", candidate))
                return int(probe.getsockname()[1])
            except OSError:
                continue
    raise SystemExit("No free TCP port could be bound on 127.0.0.1.")


def storage_directory() -> Path:
    """Where uploads go.

    Never inside the bundle: that directory is read-only on macOS (quarantine
    and app translocation) and is wiped on every start under PyInstaller's
    one-file mode. A per-user temporary directory is both writable and cleaned
    up by the operating system.
    """
    configured = os.environ.get("PEO_STORAGE_DIR")
    if configured:
        path = Path(configured).expanduser()
    else:
        path = Path(tempfile.gettempdir()) / "print-engineering-optimizer"
    path.mkdir(parents=True, exist_ok=True)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="print-engineering-optimizer",
        description="Print Engineering Optimizer - portable build.",
    )
    parser.add_argument("--port", type=int, default=8765, help="Preferred port (default: 8765)")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Interface to bind. The default is loopback only; use 0.0.0.0 to "
        "reach it from another device on your network - the application has no "
        "authentication, so only do that on a network you trust.",
    )
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser")
    args = parser.parse_args()

    root = bundle_root()

    # The bundled frontend, and the backend package when running from source.
    web = root / "web"
    if not (web / "index.html").is_file():
        candidate = root / "frontend" / "dist"
        if (candidate / "index.html").is_file():
            web = candidate
        else:
            print(
                "The web UI bundle is missing.\n"
                "Running from a source checkout? Build it first:\n"
                "    cd frontend && npm install && npm run build",
                file=sys.stderr,
            )
            return 1

    os.environ["PEO_STATIC_DIR"] = str(web)
    os.environ.setdefault("PEO_STORAGE_DIR", str(storage_directory()))

    backend = root / "backend"
    if backend.is_dir() and str(backend) not in sys.path:
        sys.path.insert(0, str(backend))

    port = free_port(args.port)
    url = f"http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{port}"

    import uvicorn  # imported here so --help stays instant

    from app.main import app  # noqa: PLC0415

    print()
    print("  Print Engineering Optimizer")
    print(f"  {url}")
    print()
    print("  Engineering-oriented estimates and FDM print optimisation guidance.")
    print("  Not a certified structural analysis system.")
    print()
    if args.host == "0.0.0.0":
        print("  Bound to all interfaces. There is no authentication - use only")
        print("  on a network you trust.")
        print()
    print("  Uploads are kept in", os.environ["PEO_STORAGE_DIR"])
    print("  Press Ctrl+C to quit.")
    print()

    if not args.no_browser:
        def open_when_ready() -> None:
            import urllib.error
            import urllib.request

            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    urllib.request.urlopen(f"{url}/health", timeout=1)
                except (urllib.error.URLError, OSError):
                    time.sleep(0.25)
                    continue
                webbrowser.open(url)
                return

        threading.Thread(target=open_when_ready, daemon=True).start()

    try:
        uvicorn.run(app, host=args.host, port=port, log_level="warning", access_log=False)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
