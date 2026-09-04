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

    import uvicorn

    from .api import create_app

    scheme = "https" if args.cert else "http"
    shown = lan_address() if args.host == "0.0.0.0" else args.host
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
