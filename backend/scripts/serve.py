#!/usr/bin/env python3
"""
Simple server for eval_labeler.html.
- GET /              → serves eval_labeler.html
- GET /file?path=... → reads any file from the filesystem (absolute or relative to CWD)
"""
import http.server
import os
import urllib.parse
from pathlib import Path

PORT = 9876
SCRIPTS_DIR = Path(__file__).parent


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/file":
            params = urllib.parse.parse_qs(parsed.query)
            path_val = params.get("path", [None])[0]
            if not path_val:
                self.send_error(400, "Missing ?path=")
                return
            target = Path(path_val)
            if not target.is_absolute():
                target = SCRIPTS_DIR / target
            if not target.exists():
                self.send_error(404, f"Not found: {target}")
                return
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        else:
            # Fall back to serving files from the scripts directory
            self.directory = str(SCRIPTS_DIR)
            super().do_GET()

    def log_message(self, fmt, *args):
        pass  # suppress request logs


if __name__ == "__main__":
    os.chdir(SCRIPTS_DIR)
    with http.server.HTTPServer(("", PORT), Handler) as httpd:
        print(f"Eval labeler: http://localhost:{PORT}/eval_labeler.html")
        httpd.serve_forever()
