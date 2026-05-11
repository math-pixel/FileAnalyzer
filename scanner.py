import os
import argparse
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Tuple, Any, Union

from utils import (
    format_size,
    save_json,
    save_html,
    log_error,
)

SYSTEM_EXCLUSIONS = [
    "$RECYCLE.BIN", "Recycler", "System Volume Information",
    "Windows", "Program Files", "Program Files (x86)",
    "ProgramData", "PerfLogs", "MSOCache",
    ".git/objects",
    "node_modules/.cache",
]


def get_default_exclusions(path: str) -> List[str]:
    path_lower = path.lower()
    if len(path_lower) <= 3:
        return SYSTEM_EXCLUSIONS.copy()
    return []


def scan_directory_iterative(
    root_path: Path,
    max_depth: Optional[int] = None,
    min_size: Optional[int] = None,
    exclude_patterns: Optional[List[str]] = None,
    progress_callback=None,
    errors: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Iterative scan using explicit stack to avoid recursion limit."""
    if errors is None:
        errors = []

    root_key = str(root_path.resolve())

    dir_data_by_key: Dict[str, Dict[str, Any]] = {}
    children_map: Dict[str, List[Dict[str, Any]]] = {}
    file_map: Dict[str, List[Dict[str, Any]]] = {}
    processed_files = 0
    current_file = ""

    stack: List[Tuple[Path, int, Optional[str]]] = [(root_path, 0, None)]
    dir_data_by_key[root_key] = {
        "name": root_path.name or str(root_path),
        "path": str(root_path),
        "type": "dir",
        "size": 0,
        "children": [],
        "file_types": {},
    }
    children_map[root_key] = []

    while stack:
        current_path, depth, parent_key = stack.pop()
        current_key = str(current_path)

        if current_key not in dir_data_by_key:
            dir_data_by_key[current_key] = {
                "name": current_path.name or str(current_path),
                "path": str(current_path),
                "type": "dir",
                "size": 0,
                "children": [],
                "file_types": {},
            }
            children_map[current_key] = []

        dir_data = dir_data_by_key[current_key]
        file_map.setdefault(current_key, [])

        if parent_key:
            children_map.setdefault(parent_key, []).append(dir_data)

        try:
            entries = list(current_path.iterdir())
        except PermissionError as e:
            log_error(errors, str(current_path), "PermissionError", str(e))
            continue
        except OSError as e:
            log_error(errors, str(current_path), "OSError", str(e))
            continue

        dir_size = 0
        file_types: Dict[str, int] = {}

        for entry in entries:
            if exclude_patterns and any(p in str(entry) for p in exclude_patterns):
                continue

            try:
                if entry.is_symlink():
                    continue

                if entry.is_dir():
                    if max_depth is None or depth < max_depth:
                        new_key = str(entry)
                        new_dir = {
                            "name": entry.name,
                            "path": str(entry),
                            "type": "dir",
                            "size": 0,
                            "children": [],
                            "file_types": {},
                        }
                        dir_data_by_key[new_key] = new_dir
                        children_map[new_key] = []
                        stack.append((entry, depth + 1, current_key))
                else:
                    size = entry.stat().st_size
                    if min_size is None or size >= min_size:
                        ext = entry.suffix.lower() or ".no_ext"
                        file_entry = {
                            "name": entry.name,
                            "path": str(entry),
                            "type": "file",
                            "size": size,
                            "extension": ext,
                        }
                        file_map[current_key].append(file_entry)
                        dir_size += size
                        file_types[ext] = file_types.get(ext, 0) + 1
                        processed_files += 1
                        current_file = entry.name

                        if progress_callback:
                            progress_callback(processed_files, current_file, dir_size)
            except PermissionError as e:
                log_error(errors, str(entry), "PermissionError", str(e))
            except OSError as e:
                log_error(errors, str(entry), "OSError", str(e))

        dir_data["size"] += dir_size
        for ext, count in file_types.items():
            dir_data["file_types"][ext] = dir_data["file_types"].get(ext, 0) + count

    for dir_key, dir_data in dir_data_by_key.items():
        dir_data["children"] = children_map.get(dir_key, [])
        dir_data["children"].sort(key=lambda x: x["size"], reverse=True)

    for dir_key in list(dir_data_by_key.keys()):
        for child in children_map.get(dir_key, []):
            pass

    root_data = dir_data_by_key[root_key]
    for dir_key, dir_data in dir_data_by_key.items():
        for file_entry in file_map.get(dir_key, []):
            dir_data["children"].append(file_entry)
        dir_data["children"].sort(key=lambda x: x["size"], reverse=True)

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
        "--exclude", "-e", nargs="+", default=None, help="Patterns to exclude"
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

    args = parser.parse_args()

    root_path = Path(args.path).resolve()
    if not root_path.exists():
        print(f"Error: path '{root_path}' does not exist")
        return 1

    system_exclusions = [] if args.no_system else get_default_exclusions(str(root_path))
    exclude_patterns = args.exclude or []
    all_exclusions = list(set(system_exclusions + exclude_patterns))

    print(f"Scanning: {root_path}")
    print(f"Options: max_depth={args.max_depth}, min_size={args.min_size}, exclude={all_exclusions}")
    print("-" * 40)
    print("Scanning...")

    start_time = datetime.now()
    errors: List[Dict[str, Any]] = []

    def progress_callback(files_done, current_name, current_size):
        if not args.no_progress:
            print(f"\r  {files_done:,} files | {format_size(current_size)} | {current_name[:50]:<50}", end="", flush=True)

    tree = scan_directory_iterative(
        root_path,
        max_depth=args.max_depth,
        min_size=args.min_size,
        exclude_patterns=all_exclusions if all_exclusions else None,
        progress_callback=progress_callback if not args.no_progress else None,
        errors=errors,
    )

    print()
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()

    total_size = tree["size"]
    total_files_found, total_dirs_found = count_items(tree)

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
            "max_depth": args.max_depth,
            "min_size": args.min_size,
            "min_size_formatted": format_size(args.min_size) if args.min_size else None,
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
    files = 0
    dirs = 0
    if node["type"] == "file":
        return 1, 0
    for child in node.get("children", []):
        f, d = count_items(child)
        files += f
        dirs += d
    return files, dirs + 1


if __name__ == "__main__":
    raise SystemExit(main())
