import http.server
import socketserver
import json
import argparse
import os
import time
import threading
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable
from urllib.parse import urlparse, parse_qs
from scanner import (
    scan_directory_parallel,
    get_default_exclusions,
    SYSTEM_EXCLUSIONS,
)
from utils import format_size


PORT = 8000
INITIAL_DEPTH = 5
PREVIEW_DEPTH = 2
CACHE_TTL = 300          # seconds (5 minutes)
CACHE_MAX_ENTRIES = 20   # evict oldest when full
SSE_PROGRESS_EVERY = 200 # files between two progress events
# Items smaller than this fraction of the scanned total are invisible in the chart
# (< 0.004°); they are merged per folder so the browser does not draw 100k+ arcs.
# Drilling into a folder rescans it, so its small items show up again at that scale.
PRUNE_FRACTION = 1e-5

OTHER_LABEL_NORMAL = "Autre espace utilisé"
OTHER_LABEL_ADVANCED = "Système / inaccessible"

_cancel_event = threading.Event()
_cancel_lock = threading.Lock()

# ────────────────────────────────────────────
# In-memory result cache
# ────────────────────────────────────────────
_scan_cache: Dict[str, Dict[str, Any]] = {}   # key -> {result, ts}
_cache_lock = threading.Lock()


def _cache_key(path: str, max_depth: Optional[int], detail_depth: Optional[int],
               min_node_size: Optional[int], no_system: bool) -> str:
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        mtime = 0
    return f"{path}|{max_depth}|{detail_depth}|{min_node_size}|{no_system}|{mtime:.2f}"


def _get_cache(key: str) -> Optional[Dict[str, Any]]:
    with _cache_lock:
        entry = _scan_cache.get(key)
        if entry and (time.monotonic() - entry["ts"]) < CACHE_TTL:
            return entry["result"]
        if entry:
            del _scan_cache[key]
    return None


def _set_cache(key: str, result: Dict[str, Any]) -> None:
    with _cache_lock:
        if len(_scan_cache) >= CACHE_MAX_ENTRIES:
            oldest = min(_scan_cache, key=lambda k: _scan_cache[k]["ts"])
            del _scan_cache[oldest]
        _scan_cache[key] = {"result": result, "ts": time.monotonic()}


# ────────────────────────────────────────────
# Cancel helpers
# ────────────────────────────────────────────
def _reset_cancel():
    with _cancel_lock:
        _cancel_event.clear()


def _request_cancel():
    with _cancel_lock:
        _cancel_event.set()


def _is_cancelled():
    return _cancel_event.is_set()


# ────────────────────────────────────────────
# Scan helpers
# ────────────────────────────────────────────
def _full_scan_params(advanced: bool, no_system: bool, depth: Optional[int] = INITIAL_DEPTH):
    """Return (max_depth, detail_depth, no_system) for a full scan.

    Normal mode stops at `depth` (deeper content is not counted) and skips system
    folders on drive roots. Advanced mode visits everything and only collapses the
    tree below `depth` for display, so every byte that can be read is accounted for.
    """
    if advanced:
        return None, (depth if depth is not None else INITIAL_DEPTH), True
    return depth, None, no_system


def _run_scan(
    scan_path_obj: Path,
    max_depth: Optional[int],
    detail_depth: Optional[int] = None,
    no_system: bool = False,
    min_node_size: Optional[int] = None,
    progress_callback=None,
    cancel_check: Callable[[], bool] = _is_cancelled,
    advanced: bool = False,
) -> Dict[str, Any]:
    """Run a scan and return the standardised result dict."""
    system_exclusions = [] if no_system else get_default_exclusions(str(scan_path_obj))
    errors: List[Dict[str, Any]] = []
    stats: Dict[str, int] = {}

    tree = scan_directory_parallel(
        scan_path_obj,
        max_depth=max_depth,
        min_size=None,
        min_node_size=min_node_size,
        exclude_patterns=system_exclusions if system_exclusions else None,
        progress_callback=progress_callback,
        errors=errors,
        cancel_check=cancel_check,
        detail_depth=detail_depth,
        stats=stats,
    )

    total_size = tree["size"]
    _summarize_and_prune(tree)

    if len(str(scan_path_obj)) <= 3:
        from utils import inject_disk_usage
        tree = inject_disk_usage(
            tree, scan_path_obj,
            other_label=OTHER_LABEL_ADVANCED if advanced else OTHER_LABEL_NORMAL,
        )

    return {
        "scan_info": {
            "root": str(scan_path_obj),
            "max_depth": max_depth,
            "detail_depth": detail_depth,
            "advanced": advanced,
            "total_size": total_size,
            "total_size_formatted": format_size(total_size),
            "total_files": stats.get("files", 0),
            "total_dirs": stats.get("dirs", 0),
            "errors_count": len(errors),
            "system_exclusions_applied": system_exclusions,
            "min_node_size": min_node_size,
        },
        "errors": errors,
        "tree": tree,
    }


def _summarize_and_prune(tree: Dict[str, Any], min_fraction: float = PRUNE_FRACTION) -> None:
    """Collect file-type counts for the whole tree into tree["file_types_total"],
    drop the per-folder copies, and merge invisible items per folder."""
    totals: Dict[str, int] = {}
    threshold = tree.get("size", 0) * min_fraction
    stack = [tree]
    while stack:
        node = stack.pop()
        for ext, n in node.pop("file_types", {}).items():
            totals[ext] = totals.get(ext, 0) + n
        children = node.get("children")
        if not children:
            continue
        if threshold > 0:
            keep = [c for c in children if c["size"] >= threshold]
            small = [c for c in children if c["size"] < threshold]
            if len(small) > 1:
                # Count file types of merged folders before they disappear
                sub = [c for c in small if c.get("type") == "dir"]
                while sub:
                    d = sub.pop()
                    for ext, n in d.get("file_types", {}).items():
                        totals[ext] = totals.get(ext, 0) + n
                    sub.extend(c for c in d.get("children", []) if c.get("type") == "dir")
                small_size = sum(c["size"] for c in small)
                if small_size > 0:
                    keep.append({
                        "name": f"({len(small)} petits éléments)",
                        "path": node["path"],
                        "type": "file",
                        "size": small_size,
                        "extension": ".misc",
                    })
                keep.sort(key=lambda x: x["size"], reverse=True)
                node["children"] = keep
        stack.extend(c for c in node["children"] if c.get("type") == "dir")
    tree["file_types_total"] = totals


def _parse_int(raw: Optional[str]) -> Optional[int]:
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def _parse_bool(raw: Optional[str]) -> bool:
    return (raw or "").lower() in ("1", "true", "yes", "on")


class FileAnalyserHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(Path(__file__).parent), **kwargs)

    def send_json(self, data: Dict, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _send_sse_event(self, event: str, data: Any):
        """Write a single SSE frame to the socket."""
        payload = json.dumps(data, ensure_ascii=False)
        frame = f"event: {event}\ndata: {payload}\n\n"
        self.wfile.write(frame.encode("utf-8"))
        self.wfile.flush()

    def _validated_dir(self, params) -> Optional[Path]:
        scan_path = params.get("path", [None])[0]
        if not scan_path:
            self.send_json({"error": "Missing path parameter"}, 400)
            return None
        scan_path_obj = Path(scan_path).resolve()
        if not scan_path_obj.exists():
            self.send_json({"error": f"Path does not exist: {scan_path}"}, 400)
            return None
        if not scan_path_obj.is_dir():
            self.send_json({"error": "Path is not a directory"}, 400)
            return None
        return scan_path_obj

    def _cached_scan(self, scan_path_obj: Path, max_depth, detail_depth, no_system,
                     min_node_size, advanced) -> Dict[str, Any]:
        key = _cache_key(str(scan_path_obj), max_depth, detail_depth, min_node_size, no_system)
        cached = _get_cache(key)
        if cached:
            return cached
        _reset_cancel()
        result = _run_scan(scan_path_obj, max_depth, detail_depth, no_system,
                           min_node_size, advanced=advanced)
        if not _is_cancelled():
            _set_cache(key, result)
        return result

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        # ── Static pages ────────────────────────────────────────────────
        if path in ("/", "/index.html", "/sunburst.html"):
            self.path = "/sunburst.html"
            super().do_GET()
            return

        # ── System exclusions list ───────────────────────────────────────
        if path == "/api/system_exclusions":
            self.send_json({"exclusions": SYSTEM_EXCLUSIONS})
            return

        # ── Drives list ──────────────────────────────────────────────────
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

        # ── Cancel ───────────────────────────────────────────────────────
        if path == "/api/cancel_scan":
            _request_cancel()
            self.send_json({"status": "cancelled"})
            return

        # ── SSE streaming scan ────────────────────────────────────────────
        if path == "/api/scan/stream":
            scan_path_obj = self._validated_dir(params)
            if scan_path_obj is None:
                return

            advanced = _parse_bool(params.get("advanced", [None])[0])
            min_node_size = _parse_int(params.get("min_node_size", [None])[0])
            max_depth, detail_depth, no_system = _full_scan_params(
                advanced, _parse_bool(params.get("no_system", [None])[0])
            )

            _reset_cancel()

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            # Progress events come from several scanner threads: serialise socket writes.
            # A failed write means the browser closed the stream: stop this scan only.
            sse_lock = threading.Lock()
            disconnected = [False]

            def send(event: str, data: Any) -> None:
                if disconnected[0]:
                    return
                with sse_lock:
                    try:
                        self._send_sse_event(event, data)
                    except OSError:
                        disconnected[0] = True

            def cancel_check() -> bool:
                return disconnected[0] or _is_cancelled()

            def progress_callback(files_done: int, current_name: str, _size: int):
                if files_done % SSE_PROGRESS_EVERY == 0:
                    send("progress", {"files_scanned": files_done, "current_file": current_name})

            def phase(event: str, p_max, p_detail) -> bool:
                key = _cache_key(str(scan_path_obj), p_max, p_detail, min_node_size, no_system)
                result = _get_cache(key)
                if result is None:
                    result = _run_scan(scan_path_obj, p_max, p_detail, no_system, min_node_size,
                                       progress_callback, cancel_check, advanced)
                    if cancel_check():
                        return False
                    _set_cache(key, result)
                send(event, result)
                return True

            try:
                # Phase 1: quick shallow preview, Phase 2: full scan
                if phase("partial", PREVIEW_DEPTH, None) and phase("done", max_depth, detail_depth):
                    return
                send("cancelled", {"message": "Scan annulé"})
            except Exception as exc:
                send("error", {"message": str(exc)})
            return

        # ── /api/scan (cache + parallel) ─────────────────────────────────
        if path == "/api/scan":
            scan_path_obj = self._validated_dir(params)
            if scan_path_obj is None:
                return
            try:
                depth: Optional[int] = int(params.get("depth", [str(INITIAL_DEPTH)])[0])
                if depth < 0:
                    depth = None
            except (ValueError, TypeError):
                depth = INITIAL_DEPTH

            advanced = _parse_bool(params.get("advanced", [None])[0])
            max_depth, detail_depth, no_system = _full_scan_params(
                advanced, _parse_bool(params.get("no_system", [None])[0]), depth
            )
            min_node_size = _parse_int(params.get("min_node_size", [None])[0])
            self.send_json(self._cached_scan(scan_path_obj, max_depth, detail_depth,
                                             no_system, min_node_size, advanced))
            return

        # ── /api/scan_dir (drill-down, cache + parallel) ─────────────────
        if path == "/api/scan_dir":
            scan_path_obj = self._validated_dir(params)
            if scan_path_obj is None:
                return
            advanced = _parse_bool(params.get("advanced", [None])[0])
            max_depth, detail_depth, no_system = _full_scan_params(
                advanced, _parse_bool(params.get("no_system", [None])[0])
            )
            min_node_size = _parse_int(params.get("min_node_size", [None])[0])
            self.send_json(self._cached_scan(scan_path_obj, max_depth, detail_depth,
                                             no_system, min_node_size, advanced))
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

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer((args.bind, args.port), FileAnalyserHandler) as httpd:
        print(f"FileAnalyser running at http://{args.bind}:{args.port}")
        print(f"Open http://{args.bind}:{args.port}/sunburst.html in your browser")
        print("Press Ctrl+C to stop")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped")


if __name__ == "__main__":
    main()
