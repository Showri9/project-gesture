"""Run the API.

    python -m gesturectl.server                    # http://localhost:8000
    python -m gesturectl.server --host 0.0.0.0     # reachable from the phone
    python -m gesturectl.server --host 0.0.0.0 --cert certs/cert.pem --key certs/key.pem

The phone needs HTTPS: getUserMedia refuses to hand over a camera to an
insecure page, and there is no exception for private network addresses. Make a
locally-trusted certificate with mkcert, install its CA on the phone once, and
serve with --cert/--key. Do not reach for a tunnel - it would send your living
room through the public internet to reach a device three metres away.
"""

from __future__ import annotations

import argparse
import socket
from pathlib import Path


def lan_address() -> str:
    """Best guess at the address the phone should type. No packet is sent."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.168.1.1", 1))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def main() -> int:
    parser = argparse.ArgumentParser(prog="gesturectl-server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--cert", type=Path, default=None)
    parser.add_argument("--key", type=Path, default=None)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    shown = lan_address() if args.host == "0.0.0.0" else args.host

    if args.cert or args.key:
        missing = [str(p) for p in (args.cert, args.key) if p is None or not p.exists()]
        if missing:
            print("\nCertificate not found:", ", ".join(missing))
            print("\nGenerate one for this machine, trusted locally:\n")
            print("  brew install mkcert")
            print("  mkcert -install")
            names = list(dict.fromkeys([shown, "localhost", "127.0.0.1"]))
            print("  mkcert -cert-file certs/cert.pem -key-file certs/key.pem \\")
            print(f"         {' '.join(names)}")
            print("\nThen install mkcert's CA on the phone once - AirDrop yourself")
            print("  ~/Library/Application Support/mkcert/rootCA.pem")
            print("and enable it under Settings > General > About >")
            print("Certificate Trust Settings.")
            print("\nOr drop --cert/--key and use http://localhost:8000 on this")
            print("machine - localhost is a secure context, so the camera works")
            print("there with no certificate at all.\n")
            return 2

    try:
        import websockets  # noqa: F401
    except ImportError:
        try:
            import wsproto  # noqa: F401
        except ImportError:
            print("\nNo WebSocket support installed.")
            print("\nBare uvicorn cannot speak WebSocket, so the pose stream and the")
            print("event stream would both answer 404 with only a warning in the log")
            print("to explain it. Install:\n")
            print("  pip install 'uvicorn[standard]'\n")
            return 2

    import uvicorn

    from .api import create_app

    scheme = "https" if args.cert else "http"
    print(f"\n  API      {scheme}://{shown}:{args.port}/api/health")
    print(f"  Phone    {scheme}://{shown}:{args.port}/")
    if scheme == "http" and args.host != "127.0.0.1":
        print("\n  NOTE: the phone's camera needs HTTPS. Over plain http the page")
        print("        will load and getUserMedia will refuse. Pass --cert/--key.")
    print()

    uvicorn.run(
        create_app(args.config),
        host=args.host,
        port=args.port,
        ssl_certfile=str(args.cert) if args.cert else None,
        ssl_keyfile=str(args.key) if args.key else None,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
