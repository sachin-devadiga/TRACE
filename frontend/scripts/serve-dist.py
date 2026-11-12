"""Minimal static server with SPA fallback, mimicking a Render static site.

Usage: python _serve_dist.py [port] [directory]
"""

from __future__ import annotations

import os
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 4173
DIRECTORY = sys.argv[2] if len(sys.argv) > 2 else "dist"


class SPAHandler(SimpleHTTPRequestHandler):
    """Serve files from the build, falling back to index.html for SPA routes."""

    def do_GET(self):  # noqa: N802 - stdlib naming
        path = self.translate_path(self.path)
        if not os.path.exists(path) or os.path.isdir(path):
            if "." not in os.path.basename(self.path.split("?")[0]):
                self.path = "/index.html"
        super().do_GET()

    def log_message(self, *args):  # silence the access log
        return


handler = partial(SPAHandler, directory=DIRECTORY)
print(f"serving {DIRECTORY} on http://127.0.0.1:{PORT}", flush=True)
ThreadingHTTPServer(("127.0.0.1", PORT), handler).serve_forever()
