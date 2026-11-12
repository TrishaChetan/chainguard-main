"""Start the whole ChainGuard demo with one command.

    python run_local.py            # chain + backend + dashboard
    python run_local.py --no-ui    # chain + backend only
    python run_local.py --open     # also open the dashboard in your browser

Does what the README's three terminals do, in order, waiting for each part
to be healthy before starting the next:

  1. compiles the contracts if blockchain/artifacts is missing
  2. starts a local Hardhat chain on 127.0.0.1:8545
  3. starts the demo backend on 127.0.0.1:8001 (deploys the registry, seeds scenarios)
  4. starts the Vite dashboard on http://localhost:5173

Ctrl+C stops all of them. Works on Windows, macOS and Linux. Run `npm ci` in
blockchain/ and frontend/ and `pip install -r requirements.txt` first.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHAIN = ROOT / "blockchain"
FRONTEND = ROOT / "frontend"
ARTIFACT = CHAIN / "artifacts" / "contracts" / "AgentTrustRegistry.sol" / "AgentTrustRegistry.json"
RPC_URL = "http://127.0.0.1:8545"
API_URL = "http://127.0.0.1:8001"
UI_URL = "http://localhost:5173"   # backend CORS allows localhost:5173 (not 127.0.0.1)

NPM = "npm.cmd" if os.name == "nt" else "npm"
procs: list[tuple[str, subprocess.Popen]] = []


def say(msg: str) -> None:
    print(f"[chainguard] {msg}", flush=True)


def env() -> dict:
    e = dict(os.environ)
    e.setdefault("CHAINGUARD_NETWORK", "hardhat")
    e.setdefault("CHAINGUARD_RPC_URL", RPC_URL)
    e.setdefault("VITE_API_BASE_URL", API_URL)
    return e


def start(name: str, cmd: list[str], cwd: Path) -> subprocess.Popen:
    kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    p = subprocess.Popen(cmd, cwd=cwd, env=env(), **kw)
    procs.append((name, p))
    return p


def stop_all() -> None:
    for name, p in reversed(procs):
        if p.poll() is None:
            say(f"stopping {name}")
            try:
                if os.name == "nt":
                    p.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    os.killpg(p.pid, signal.SIGTERM)
                p.wait(timeout=10)
            except Exception:
                p.kill()


def rpc_ready() -> bool:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}).encode()
    req = urllib.request.Request(RPC_URL, data=body, headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def api_ready() -> bool:
    try:
        with urllib.request.urlopen(f"{API_URL}/api/health", timeout=3) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2):
            return True
    except (urllib.error.URLError, OSError):
        return False


def wait_for(what: str, check, proc: subprocess.Popen, timeout: float) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check():
            return
        if proc.poll() is not None:
            raise SystemExit(f"[chainguard] {what} exited with code {proc.returncode} — see its output above")
        time.sleep(0.5)
    raise SystemExit(f"[chainguard] {what} did not become ready within {int(timeout)}s")


def preflight(no_ui: bool) -> None:
    missing = []
    if not shutil.which(NPM):
        missing.append("Node.js/npm (https://nodejs.org)")
    if not (CHAIN / "node_modules").exists():
        missing.append("blockchain dependencies — run: cd blockchain && npm ci")
    if not no_ui and not (FRONTEND / "node_modules").exists():
        missing.append("frontend dependencies — run: cd frontend && npm ci")
    try:
        import web3  # noqa: F401
    except ImportError:
        missing.append("Python packages — run: pip install -r requirements.txt")
    if missing:
        raise SystemExit("[chainguard] missing:\n  - " + "\n  - ".join(missing))
    for url, label in ((RPC_URL, "port 8545"), (API_URL, "port 8001")):
        if (rpc_ready() if url == RPC_URL else http_ok(url)):
            raise SystemExit(f"[chainguard] something is already running on {label}; stop it first")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Start the ChainGuard demo (chain + backend + dashboard).")
    ap.add_argument("--no-ui", action="store_true", help="don't start the frontend dev server")
    ap.add_argument("--open", action="store_true", help="open the dashboard in a browser when ready")
    args = ap.parse_args(argv)

    preflight(args.no_ui)

    # Turn Ctrl+C *and* SIGTERM (kill, docker stop, closing the terminal) into a
    # clean shutdown. SIGINT is re-armed explicitly because shells that start us
    # in the background set it to "ignore".
    def _interrupt(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGINT, _interrupt)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _interrupt)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, _interrupt)

    try:
        if not ARTIFACT.exists():
            say("compiling contracts (first run)…")
            if subprocess.run([NPM, "run", "compile"], cwd=CHAIN, env=env()).returncode != 0:
                raise SystemExit("[chainguard] contract compilation failed")

        say("starting Hardhat chain…")
        chain = start("hardhat", [NPM, "run", "node"], CHAIN)
        wait_for("Hardhat chain", rpc_ready, chain, 90)
        say(f"chain ready at {RPC_URL}")

        say("starting backend (deploys the registry and seeds demo scenarios)…")
        api = start("backend", [sys.executable, "-m", "backend.api.demo_server"], ROOT)
        wait_for("Backend", api_ready, api, 240)
        say(f"backend ready at {API_URL}")

        if not args.no_ui:
            say("starting dashboard…")
            ui = start("dashboard", [NPM, "run", "dev", "--", "--port", "5173", "--strictPort"], FRONTEND)
            wait_for("Dashboard", lambda: http_ok(UI_URL), ui, 90)
            say(f"dashboard ready at {UI_URL}")
            if args.open:
                webbrowser.open(UI_URL)

        say("all services running — press Ctrl+C to stop")
        while all(p.poll() is None for _, p in procs):
            time.sleep(1)
        dead = next(n for n, p in procs if p.poll() is not None)
        say(f"{dead} stopped unexpectedly — shutting down the rest")
    except KeyboardInterrupt:
        print()
    finally:
        stop_all()


if __name__ == "__main__":
    main()
