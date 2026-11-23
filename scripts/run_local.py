import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
HOST = "127.0.0.1"
HTTP = build_opener(ProxyHandler({}))


def available_port(first: int) -> int:
    for port in range(first, first + 15):
        with socket.socket() as candidate:
            candidate.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                candidate.bind((HOST, port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"No local port available between {first} and {first + 14}.")


def wait_for(url: str, child: subprocess.Popen, expected: int = 200) -> bytes:
    deadline = time.monotonic() + 35
    last_issue = "connection unavailable"
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError(f"Server exited with status {child.returncode} while opening {url}.")
        try:
            with HTTP.open(url, timeout=5) as response:
                if response.status == expected:
                    return response.read()
                last_issue = f"HTTP {response.status}"
        except HTTPError as exc:
            if exc.code == expected:
                return b""
            last_issue = f"HTTP {exc.code}"
        except (OSError, URLError) as exc:
            last_issue = str(exc.reason if isinstance(exc, URLError) else exc)
        time.sleep(0.5)
    raise RuntimeError(f"{url} did not become ready ({last_issue}).")


def stop(child: subprocess.Popen) -> None:
    if child.poll() is None:
        try:
            child.send_signal(signal.CTRL_BREAK_EVENT)
            child.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            subprocess.run(["taskkill.exe", "/PID", str(child.pid), "/T", "/F"], check=False, capture_output=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Start both PortPair servers.")
    parser.add_argument("--check", action="store_true", help="Check both servers and then stop them.")
    args = parser.parse_args()
    children = []
    try:
        node = shutil.which("node")
        if not node or not (WEB / "node_modules" / "next" / "dist" / "bin" / "next").is_file():
            raise RuntimeError("Node.js and npm dependencies are required. See local setup in README.md.")
        subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "setup_storefront.py")], cwd=ROOT, check=True)
        api_port, web_port = available_port(8010), available_port(3010)
        api_url, web_url = f"http://{HOST}:{api_port}", f"http://{HOST}:{web_port}"
        env = {**os.environ, "NEXT_TELEMETRY_DISABLED": "1", "PORTPAIR_API_URL": api_url}
        creation = subprocess.CREATE_NEW_PROCESS_GROUP
        api = subprocess.Popen(
            [sys.executable, "-B", "-m", "uvicorn", "shopbot.api:app", "--host", HOST, "--port", str(api_port)],
            cwd=ROOT, env=env, creationflags=creation,
        )
        children.append(api)
        mode = "start" if (WEB / ".next" / "BUILD_ID").is_file() else "dev"
        web = subprocess.Popen(
            [node, str(WEB / "node_modules" / "next" / "dist" / "bin" / "next"), mode, "--hostname", HOST, "--port", str(web_port)],
            cwd=WEB, env=env, creationflags=creation,
        )
        children.append(web)
        if wait_for(f"{api_url}/health", api).strip() != b"ok":
            raise RuntimeError("The API returned an unexpected health response.")
        if b"PortPair" not in wait_for(web_url, web):
            raise RuntimeError("The storefront returned an unexpected page.")
        if not json.loads(wait_for(f"{web_url}/api/store/catalog", web)):
            raise RuntimeError("The storefront did not receive any products from the API.")
        wait_for(f"{web_url}/seller", web)
        wait_for(f"{web_url}/api/store/seller/dashboard", web, expected=401)
        print(f"\nStorefront: {web_url}/\nSeller: {web_url}/seller\nAPI: {api_url}/health", flush=True)
        if args.check:
            print("Storefront, catalog, seller sign-in gate, and API checks passed.", flush=True)
            return 0
        print("Leave this window open. Press Ctrl+C to stop PortPair.", flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
        raise RuntimeError("A PortPair server stopped. Check the server output above.")
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"PortPair startup failed: {exc}", file=sys.stderr)
        return 1
    finally:
        for child in reversed(children):
            stop(child)


if __name__ == "__main__":
    raise SystemExit(main())
