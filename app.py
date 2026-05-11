import http.server
import socketserver
import json
import argparse
import os
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse, parse_qs
from scanner import scan_directory_iterative, get_default_exclusions, SYSTEM_EXCLUSIONS, count_items
from utils import format_size


PORT = 8000
INITIAL_DEPTH = 5


class FileAnalyserHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(Path(__file__).parent), **kwargs)

    def send_json(self, data: Dict, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html" or path == "/sunburst.html":
            self.path = "/sunburst.html"
            super().do_GET()
            return

        if path == "/api/system_exclusions":
            self.send_json({"exclusions": SYSTEM_EXCLUSIONS})
            return

        if path == "/api/scan":
            params = parse_qs(parsed.query)
            scan_path = params.get("path", [None])[0]
            depth = params.get("depth", [str(INITIAL_DEPTH)])[0]
            no_system = params.get("no_system", ["false"])[0]

            if not scan_path:
                self.send_json({"error": "Missing path parameter"}, 400)
                return

            scan_path_obj = Path(scan_path).resolve()
            if not scan_path_obj.exists():
                self.send_json({"error": f"Path does not exist: {scan_path}"}, 400)
                return

            max_depth = int(depth) if depth and depth.isdigit() else INITIAL_DEPTH
            system_exclusions = [] if no_system == "true" else get_default_exclusions(str(scan_path_obj))

            errors: List[Dict[str, Any]] = []

            def progress_callback(files_done, current_name, current_size):
                pass

            tree = scan_directory_iterative(
                scan_path_obj,
                max_depth=max_depth,
                min_size=None,
                exclude_patterns=system_exclusions if system_exclusions else None,
                progress_callback=progress_callback,
                errors=errors,
            )

            total_size = tree["size"]
            total_files, total_dirs = count_items(tree)

            result = {
                "scan_info": {
                    "root": str(scan_path_obj),
                    "max_depth": max_depth,
                    "total_size": total_size,
                    "total_size_formatted": format_size(total_size),
                    "total_files": total_files,
                    "total_dirs": total_dirs,
                    "errors_count": len(errors),
                    "system_exclusions_applied": system_exclusions,
                },
                "errors": errors,
                "tree": tree,
            }

            self.send_json(result)
            return

        if path == "/api/scan_dir":
            params = parse_qs(parsed.query)
            scan_path = params.get("path", [None])[0]
            no_system = params.get("no_system", ["false"])[0]

            if not scan_path:
                self.send_json({"error": "Missing path parameter"}, 400)
                return

            scan_path_obj = Path(scan_path).resolve()
            if not scan_path_obj.exists():
                self.send_json({"error": f"Path does not exist: {scan_path}"}, 400)
                return

            if not scan_path_obj.is_dir():
                self.send_json({"error": "Path is not a directory"}, 400)
                return

            system_exclusions = [] if no_system == "true" else get_default_exclusions(str(scan_path_obj))

            errors: List[Dict[str, Any]] = []

            def progress_callback(files_done, current_name, current_size):
                pass

            tree = scan_directory_iterative(
                scan_path_obj,
                max_depth=None,
                min_size=None,
                exclude_patterns=system_exclusions if system_exclusions else None,
                progress_callback=progress_callback,
                errors=errors,
            )

            self.send_json({"tree": tree, "errors": errors})
            return

        if path.startswith("/api/drives"):
            drives = []
            if os.name == "nt":
                import string
                for letter in string.ascii_uppercase:
                    drive = f"{letter}:\\"
                    if Path(drive).exists():
                        drives.append(drive)
            else:
                drives.append("/")
            self.send_json({"drives": drives})
            return

        super().do_GET()

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description="FileAnalyser Web Server")
    parser.add_argument(
        "--port", "-p", type=int, default=PORT, help=f"Port to listen on (default: {PORT})"
    )
    parser.add_argument(
        "--bind", "-b", default="127.0.0.1", help="IP to bind to (default: 127.0.0.1)"
    )
    args = parser.parse_args()

    with socketserver.TCPServer((args.bind, args.port), FileAnalyserHandler) as httpd:
        print(f"FileAnalyser running at http://{args.bind}:{args.port}")
        print(f"Open http://{args.bind}:{args.port}/sunburst.html in your browser")
        print("Press Ctrl+C to stop")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped")


if __name__ == "__main__":
    main()
