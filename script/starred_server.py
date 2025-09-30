#!/usr/bin/env python3
"""Development server for starred_list.html.

This script extends ``SimpleHTTPRequestHandler`` to handle POST requests to
``/starred_list.json`` so the starred firmware selections can be persisted
locally while still serving the static assets required by the page.
"""

from __future__ import annotations

import argparse
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


class StarredRequestHandler(SimpleHTTPRequestHandler):
    """Serve static files and persist starred firmware selections."""

    def do_POST(self) -> None:  # noqa: N802 (matching BaseHTTPRequestHandler API)
        if self.path != "/starred_list.json":
            self.send_error(405, "Unsupported POST target")
            return

        content_length = int(self.headers.get("Content-Length", "0") or 0)
        raw_body = self.rfile.read(content_length)

        try:
            data = json.loads(raw_body.decode("utf-8") or "[]")
        except json.JSONDecodeError as exc:
            self.send_error(400, "Invalid JSON payload", explain=str(exc))
            return

        filtered = []
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                if item.get("star") != 1:
                    continue
                fid = item.get("fid")
                if not fid:
                    continue
                filtered.append(
                    {
                        "fid": str(fid),
                        "name": str(item.get("name", "")),
                        "author": str(item.get("author", "")),
                        "star": 1,
                    }
                )

        target_path = Path(self.translate_path(self.path))
        target_path.write_text(json.dumps(filtered, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        response: dict[str, Any] = {"saved": len(filtered)}

        body = json.dumps(response, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve starred_list.html with write support")
    parser.add_argument("--host", default="127.0.0.1", help="Host/IP to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    parser.add_argument(
        "--directory",
        default=Path(__file__).resolve().parents[1],
        type=Path,
        help="Base directory to serve (default: repository root)",
    )

    args = parser.parse_args()
    directory = args.directory.resolve()

    handler = partial(StarredRequestHandler, directory=str(directory))
    server = ThreadingHTTPServer((args.host, args.port), handler)

    print(f"Serving {directory} on http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop the server.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
