import os
import threading
import argparse
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Tuple, Any, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from utils import (
    format_size,
    save_json,
    save_html,
    log_error,
)

# Skipped when scanning a drive root in normal mode.
# Plain names only match entries directly under the scanned root (so
# "Windows" excludes C:\Windows, not C:\Users\x\AppData\...\Windows).
# Patterns containing '/' match as a path fragment at any depth.
SYSTEM_EXCLUSIONS = [
    "$RECYCLE.BIN", "Recycler", "System Volume Information",
    "Windows", "Program Files", "Program Files (x86)",
    "ProgramData", "PerfLogs", "MSOCache",
    ".git/objects",
    "node_modules/.cache",
]

DETAIL_DEPTH_DEFAULT = 5

_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_REPARSE_TAG_NAME_SURROGATE = 0x20000000   # set on junctions, mount points, symlinks
# Cloud placeholders (OneDrive "online only" etc.) whose data is not on the disk
_CLOUD_ONLY_ATTRS = 0x00001000 | 0x00040000 | 0x00400000  # OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS


def get_default_exclusions(path: str) -> List[str]:
    path_lower = path.lower()
    if len(path_lower) <= 3:
        return SYSTEM_EXCLUSIONS.copy()
    return []


def _is_junction(path: str) -> bool:
    """Detect NTFS junction points on Windows (not caught by os.path.islink)."""
    if os.name != 'nt':
        return False
    try:
        import ctypes
        attrs = ctypes.windll.kernel32.GetFileAttributesW(path)
        return attrs != -1 and bool(attrs & _FILE_ATTRIBUTE_REPARSE_POINT)
    except Exception:
        return False


def _is_link_like(entry: os.DirEntry) -> bool:
    """True for symlinks and junctions/mount points.

    Following them would count the same data twice or loop forever. Other
    reparse points (OneDrive folders, dedup files...) hold real data and are kept.
    On Windows, DirEntry.stat(follow_symlinks=False) is served from the directory
    listing, so this costs no extra system call.
    """
    if entry.is_symlink():
        return True
    if os.name != "nt":
        return False
    st = entry.stat(follow_symlinks=False)
    if not (getattr(st, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT):
        return False
    tag = getattr(st, "st_reparse_tag", 0)
    if tag:
        return bool(tag & _REPARSE_TAG_NAME_SURROGATE)
    # Tag unknown: skip directories to be safe from loops, keep files
    return entry.is_dir(follow_symlinks=False)


def _split_exclusions(patterns: Optional[List[str]]) -> Tuple[set, List[str]]:
    """Split patterns into root-level names and path fragments."""
    names: set = set()
    fragments: List[str] = []
    for p in patterns or []:
        norm = p.replace("\\", "/").strip("/").lower()
        if not norm:
            continue
        if "/" in norm:
            fragments.append("/" + norm + "/")
        else:
            names.add(norm)
    return names, fragments


def _fragment_patterns(patterns: Optional[List[str]]) -> List[str]:
    return [p for p in (patterns or []) if "/" in p.replace("\\", "/").strip("/")]


def _is_excluded(entry: os.DirEntry, at_root: bool, names: set, fragments: List[str]) -> bool:
    if at_root and entry.name.lower() in names:
        return True
    if fragments:
        norm = "/" + entry.path.replace("\\", "/").lower() + "/"
        return any(f in norm for f in fragments)
    return False


def _new_dir_node(name: str, path: str) -> Dict[str, Any]:
    return {
        "name": name,
        "path": path,
        "type": "dir",
        "size": 0,
        "children": [],
        "file_types": {},
    }


def _finalize_children(node: Dict[str, Any], min_node_size: Optional[int]) -> None:
    """Attach collected files to a dir node (grouping small ones) and sort children."""
    files = node.pop("_files", [])
    if min_node_size is not None and files:
        small = [f for f in files if f["size"] < min_node_size]
        files = [f for f in files if f["size"] >= min_node_size]
        if small:
            files.append({
                "name": f"({len(small)} petits fichiers)",
                "path": node["path"],
                "type": "file",
                "size": sum(f["size"] for f in small),
                "extension": ".misc",
            })
    node["children"].extend(files)
    node["children"].sort(key=lambda x: x["size"], reverse=True)


def _propagate_sizes(node: Dict[str, Any]) -> None:
    """Propagate file sizes from children up to parent directories (post-order, iterative)."""
    stack: List[Tuple[Dict[str, Any], bool]] = [(node, False)]
    while stack:
        current, processed = stack.pop()
        if current.get("type") == "file":
            continue
        if processed:
            for child in current.get("children", []):
                if child.get("type") == "dir":
                    current["size"] += child["size"]
        else:
            stack.append((current, True))
            for child in current.get("children", []):
                if child.get("type") == "dir":
                    stack.append((child, False))


def _add_stats(stats: Optional[Dict[str, int]], files: int, dirs: int) -> None:
    if stats is not None:
        stats["files"] = stats.get("files", 0) + files
        stats["dirs"] = stats.get("dirs", 0) + dirs


def scan_directory_iterative(
    root_path: Path,
    max_depth: Optional[int] = None,
    min_size: Optional[int] = None,
    min_node_size: Optional[int] = None,
    exclude_patterns: Optional[List[str]] = None,
    progress_callback=None,
    errors: Optional[List[Dict[str, Any]]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    detail_depth: Optional[int] = None,
    stats: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """Iterative scan using an explicit stack (no recursion limit).

    max_depth:     directories deeper than this are not visited at all (their size is lost).
    detail_depth:  directories at this depth are still fully traversed and their size
                   counted, but they appear as a single leaf node ("collapsed": True)
                   so the tree stays small enough for the browser. None = full detail.
    min_node_size: files smaller than this are grouped into one '(N petits fichiers)'
                   node per directory.
    stats:         if given, receives {"files": n, "dirs": n} (counts include collapsed parts).
    """
    if errors is None:
        errors = []
    root_names, fragments = _split_exclusions(exclude_patterns)

    root_str = str(root_path)
    root_data = _new_dir_node(root_path.name or root_str, root_str)
    if detail_depth is not None and detail_depth <= 0:
        root_data["collapsed"] = True

    all_nodes: List[Dict[str, Any]] = [root_data]
    processed_files = 0
    dir_count = 1

    # (directory path, depth, node that receives this directory's sizes)
    stack: List[Tuple[str, int, Dict[str, Any]]] = [(root_str, 0, root_data)]

    while stack:
        if cancel_check and cancel_check():
            break

        current_str, depth, node = stack.pop()
        detailed = detail_depth is None or depth < detail_depth

        try:
            with os.scandir(current_str) as it:
                entries = list(it)
        except OSError as e:
            log_error(errors, current_str, type(e).__name__, str(e))
            continue

        dir_size = 0
        file_types = node["file_types"]

        for entry in entries:
            try:
                if _is_excluded(entry, depth == 0, root_names, fragments):
                    continue
                if _is_link_like(entry):
                    continue

                if entry.is_dir(follow_symlinks=False):
                    if max_depth is not None and depth >= max_depth:
                        continue
                    dir_count += 1
                    if detailed:
                        child = _new_dir_node(entry.name, entry.path)
                        if detail_depth is not None and depth + 1 >= detail_depth:
                            child["collapsed"] = True
                        node["children"].append(child)
                        all_nodes.append(child)
                        stack.append((entry.path, depth + 1, child))
                    else:
                        # Below detail depth: keep accumulating into the collapsed ancestor
                        stack.append((entry.path, depth + 1, node))
                else:
                    st = entry.stat(follow_symlinks=False)
                    if getattr(st, "st_file_attributes", 0) & _CLOUD_ONLY_ATTRS:
                        continue
                    size = st.st_size
                    if min_size is not None and size < min_size:
                        continue
                    ext = os.path.splitext(entry.name)[1].lower() or ".no_ext"
                    dir_size += size
                    file_types[ext] = file_types.get(ext, 0) + 1
                    processed_files += 1
                    if detailed:
                        node.setdefault("_files", []).append({
                            "name": entry.name,
                            "path": entry.path,
                            "type": "file",
                            "size": size,
                            "extension": ext,
                        })
                    if progress_callback:
                        progress_callback(processed_files, entry.name, dir_size)
            except OSError as e:
                log_error(errors, entry.path, type(e).__name__, str(e))

        node["size"] += dir_size

    for n in all_nodes:
        _finalize_children(n, min_node_size)

    # Every dir now reflects its full subtree
    _propagate_sizes(root_data)

    _add_stats(stats, processed_files, dir_count)
    return root_data


def scan_directory_parallel(
    root_path: Path,
    max_depth: Optional[int] = None,
    min_size: Optional[int] = None,
    min_node_size: Optional[int] = None,
    exclude_patterns: Optional[List[str]] = None,
    progress_callback=None,
    errors: Optional[List[Dict[str, Any]]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    n_workers: int = 8,
    detail_depth: Optional[int] = None,
    stats: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """Parallel scan: each root-level subdirectory is scanned in its own thread.

    Same parameters and result as scan_directory_iterative.
    """
    if errors is None:
        errors = []

    if (max_depth is not None and max_depth <= 0) or (detail_depth is not None and detail_depth <= 0):
        return scan_directory_iterative(
            root_path, max_depth=max_depth, min_size=min_size, min_node_size=min_node_size,
            exclude_patterns=exclude_patterns, progress_callback=progress_callback,
            errors=errors, cancel_check=cancel_check, detail_depth=detail_depth, stats=stats,
        )

    root_names, fragments = _split_exclusions(exclude_patterns)
    # Root-level names only apply here; sub-scans get the path fragments only
    sub_patterns = _fragment_patterns(exclude_patterns) or None

    root_str = str(root_path)
    root_data = _new_dir_node(root_path.name or root_str, root_str)

    counter = [0]
    counter_lock = threading.Lock()
    errors_lock = threading.Lock()

    def report(name: str):
        if progress_callback:
            with counter_lock:
                counter[0] += 1
                total = counter[0]
            progress_callback(total, name, 0)

    try:
        with os.scandir(root_str) as it:
            entries = list(it)
    except OSError as e:
        log_error(errors, root_str, type(e).__name__, str(e))
        _add_stats(stats, 0, 1)
        return root_data

    dir_entries: List[os.DirEntry] = []
    local_files = 0

    for entry in entries:
        if cancel_check and cancel_check():
            break
        try:
            if _is_excluded(entry, True, root_names, fragments):
                continue
            if _is_link_like(entry):
                continue
            if entry.is_dir(follow_symlinks=False):
                dir_entries.append(entry)
            else:
                st = entry.stat(follow_symlinks=False)
                if getattr(st, "st_file_attributes", 0) & _CLOUD_ONLY_ATTRS:
                    continue
                size = st.st_size
                if min_size is not None and size < min_size:
                    continue
                ext = os.path.splitext(entry.name)[1].lower() or ".no_ext"
                root_data.setdefault("_files", []).append({
                    "name": entry.name,
                    "path": entry.path,
                    "type": "file",
                    "size": size,
                    "extension": ext,
                })
                root_data["size"] += size
                root_data["file_types"][ext] = root_data["file_types"].get(ext, 0) + 1
                local_files += 1
                report(entry.name)
        except OSError as e:
            log_error(errors, entry.path, type(e).__name__, str(e))

    _finalize_children(root_data, min_node_size)

    next_depth = None if max_depth is None else max_depth - 1
    next_detail = None if detail_depth is None else detail_depth - 1
    total_files = local_files
    total_dirs = 1

    def scan_subdir(entry: os.DirEntry) -> Optional[Tuple[Dict[str, Any], Dict[str, int]]]:
        if cancel_check and cancel_check():
            return None
        sub_errors: List[Dict[str, Any]] = []
        sub_stats: Dict[str, int] = {}
        subtree = scan_directory_iterative(
            Path(entry.path),
            max_depth=next_depth,
            min_size=min_size,
            min_node_size=min_node_size,
            exclude_patterns=sub_patterns,
            progress_callback=(lambda _n, name, _s: report(name)) if progress_callback else None,
            errors=sub_errors,
            cancel_check=cancel_check,
            detail_depth=next_detail,
            stats=sub_stats,
        )
        subtree["name"] = entry.name
        with errors_lock:
            errors.extend(sub_errors)
        return subtree, sub_stats

    if dir_entries:
        with ThreadPoolExecutor(max_workers=n_workers) as executor:
            future_map = {executor.submit(scan_subdir, e): e for e in dir_entries}
            for future in as_completed(future_map):
                try:
                    res = future.result()
                except Exception as e:
                    log_error(errors, future_map[future].path, "ScanError", str(e))
                    continue
                if res is None:
                    continue
                subtree, sub_stats = res
                root_data["children"].append(subtree)
                root_data["size"] += subtree["size"]
                total_files += sub_stats.get("files", 0)
                total_dirs += sub_stats.get("dirs", 0)

    root_data["children"].sort(key=lambda x: x["size"], reverse=True)
    _add_stats(stats, total_files, total_dirs)
    return root_data


def main():
    parser = argparse.ArgumentParser(description="FileAnalyser - Disk Scanner")
    parser.add_argument("path", help="Root path to scan")
    parser.add_argument(
        "--output", "-o", default="output/scan_result.json", help="Output JSON path"
    )
    parser.add_argument(
        "--max-depth", "-d", type=int, default=None, help="Maximum depth to scan"
    )
    parser.add_argument(
        "--min-size", "-m", type=int, default=None, help="Minimum file size in bytes"
    )
    parser.add_argument(
        "--min-node-size", type=int, default=None,
        help="Files smaller than this (bytes) are grouped into an aggregate node"
    )
    parser.add_argument(
        "--exclude", "-e", nargs="+", default=None,
        help="Patterns to exclude (plain name = entry directly under the root, "
             "pattern with '/' = path fragment anywhere)"
    )
    parser.add_argument(
        "--strict", action="store_true", help="Stop on first error"
    )
    parser.add_argument(
        "--html", action="store_true", help="Generate HTML visualization"
    )
    parser.add_argument(
        "--no-progress", action="store_true", help="Disable progress bar"
    )
    parser.add_argument(
        "--no-system", action="store_true", help="Include system folders"
    )
    parser.add_argument(
        "--advanced", action="store_true",
        help="Scan everything: system folders included, no depth limit "
             f"(tree collapsed below depth {DETAIL_DEPTH_DEFAULT})"
    )
    parser.add_argument(
        "--sequential", action="store_true", help="Disable parallel scan"
    )

    args = parser.parse_args()

    root_path = Path(args.path).resolve()
    if not root_path.exists():
        print(f"Error: path '{root_path}' does not exist")
        return 1

    max_depth = args.max_depth
    detail_depth = None
    no_system = args.no_system
    if args.advanced:
        max_depth = None
        detail_depth = DETAIL_DEPTH_DEFAULT
        no_system = True

    system_exclusions = [] if no_system else get_default_exclusions(str(root_path))
    exclude_patterns = args.exclude or []
    all_exclusions = list(set(system_exclusions + exclude_patterns))

    print(f"Scanning: {root_path}")
    print(f"Options: max_depth={max_depth}, detail_depth={detail_depth}, "
          f"min_size={args.min_size}, exclude={all_exclusions}")
    print("-" * 40)
    print("Scanning...")

    start_time = datetime.now()
    errors: List[Dict[str, Any]] = []
    stats: Dict[str, int] = {}

    def progress_callback(files_done, current_name, current_size):
        if not args.no_progress:
            print(f"\r  {files_done:,} files | {current_name[:50]:<50}", end="", flush=True)

    scanner_fn = scan_directory_iterative if args.sequential else scan_directory_parallel
    tree = scanner_fn(
        root_path,
        max_depth=max_depth,
        min_size=args.min_size,
        min_node_size=args.min_node_size,
        exclude_patterns=all_exclusions if all_exclusions else None,
        progress_callback=progress_callback if not args.no_progress else None,
        errors=errors,
        detail_depth=detail_depth,
        stats=stats,
    )

    print()
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()

    total_size = tree["size"]
    total_files_found = stats.get("files", 0)
    total_dirs_found = stats.get("dirs", 0)

    from utils import inject_disk_usage
    tree = inject_disk_usage(tree, root_path)

    scan_info = {
        "root": str(root_path),
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "duration_seconds": duration,
        "total_size": total_size,
        "total_size_formatted": format_size(total_size),
        "total_files": total_files_found,
        "total_dirs": total_dirs_found,
        "errors_count": len(errors),
        "options": {
            "max_depth": max_depth,
            "detail_depth": detail_depth,
            "advanced": args.advanced,
            "min_size": args.min_size,
            "min_size_formatted": format_size(args.min_size) if args.min_size else None,
            "min_node_size": args.min_node_size,
            "exclude_patterns": all_exclusions,
            "system_exclusions_applied": system_exclusions,
        },
    }

    result = {
        "scan_info": scan_info,
        "errors": errors,
        "tree": tree,
    }

    save_json(result, args.output)
    print(f"Saved: {args.output}")
    print("-" * 40)
    print(f"Total size: {format_size(total_size)}")
    print(f"Files: {total_files_found:,}")
    print(f"Directories: {total_dirs_found:,}")
    print(f"Errors: {len(errors)}")
    print(f"Duration: {duration:.1f}s")

    if errors:
        print("\nErrors:")
        for err in errors:
            print(f"  [{err['type']}] {err['path']}: {err['message']}")

    if args.html:
        html_output = args.output.replace(".json", ".html")
        save_html(html_output, args.output, tree)
        print(f"Generated: {html_output}")

    return 0


def count_items(node: Dict[str, Any]) -> Tuple[int, int]:
    """Iterative count of file and directory nodes present in the tree."""
    files = 0
    dirs = 0
    stack: List[Dict[str, Any]] = [node]
    while stack:
        current = stack.pop()
        if current.get("type") == "file":
            files += 1
        else:
            dirs += 1
            stack.extend(current.get("children", []))
    return files, dirs


if __name__ == "__main__":
    raise SystemExit(main())
