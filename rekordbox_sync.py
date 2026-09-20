#!/usr/bin/env python3
"""
Rekordbox to Apple Music Sync with Folder Structure

Reads a Rekordbox collection XML export and mirrors playlists with their
original folder structure into Apple Music. All playlists are placed under
a main "REKORDBOX" folder in Music.app.

Usage:
    python3 rekordbox_sync.py --xml /path/to/rekordbox_export.xml --dry-run
    python3 rekordbox_sync.py --xml /path/to/rekordbox_export.xml
"""

import argparse
import datetime
import json
import subprocess
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Dict


STATE_FILE_NAME = "rekordbox_sync_state.json"


# ANSI color codes for terminal output
class Colors:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"
    END = "\033[0m"
    GRAY = "\033[90m"


def print_header(text: str) -> None:
    """Print a formatted header."""
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'=' * 70}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.CYAN}{text:^70}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 70}{Colors.END}\n")


def print_section(text: str) -> None:
    """Print a section title."""
    print(f"{Colors.BOLD}{Colors.BLUE}➜ {text}{Colors.END}")


def print_success(text: str) -> None:
    """Print a success message."""
    print(f"  {Colors.GREEN}✓{Colors.END} {text}")


def print_error(text: str) -> None:
    """Print an error message."""
    print(f"  {Colors.RED}✗{Colors.END} {text}")


def print_info(text: str) -> None:
    """Print an info message."""
    print(f"  {Colors.CYAN}ℹ{Colors.END} {text}")


def print_warning(text: str) -> None:
    """Print a warning message."""
    print(f"  {Colors.YELLOW}⚠{Colors.END} {text}")


def print_step(text: str) -> None:
    """Print a step."""
    print(f"  {Colors.GRAY}→{Colors.END} {text}")


def run_apple_script(script: str) -> str:
    """Execute AppleScript and return output."""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"AppleScript error: {result.stderr.strip()}")
    return result.stdout.strip()


def escape_for_applescript(s: str) -> str:
    """Escape a string for safe use in AppleScript."""
    s = s.replace("\r\n", " ").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return s.replace("\\", "\\\\").replace('"', '\\"')


def parse_rekordbox_xml(xml_path: Path) -> dict:
    """Parse Rekordbox XML and extract playlist structure with file locations."""
    print_step(f"Parsing {xml_path.name}...")

    tree = ET.parse(xml_path)
    root = tree.getroot()

    # Find all TRACK elements by ID for quick lookup, including file locations
    tracks = {}
    for track_elem in root.findall(".//TRACK"):
        track_id = track_elem.get("TrackID")
        name = track_elem.get("Name", "Unknown")
        artist = track_elem.get("Artist", "Unknown")
        location = track_elem.get("Location", "")
        
        if track_id and location:
            # Decode URL-encoded location and convert to POSIX path
            try:
                decoded_location = urllib.parse.unquote(location)
                if decoded_location.startswith("file://localhost"):
                    posix_path = decoded_location.replace("file://localhost", "")
                else:
                    posix_path = decoded_location
                tracks[track_id] = {
                    "name": name,
                    "artist": artist,
                    "path": posix_path
                }
            except Exception:
                pass

    print_info(f"Found {len(tracks)} tracks in collection")

    # Extract playlists from PLAYLISTS section
    playlists_data = {}

    def traverse_nodes(node, path_tuple=()):
        """Recursively traverse NODE tree structure."""
        node_name = node.get("Name", "").replace("&apos;", "'").replace("&quot;", '"').replace("&amp;", "&")
        node_type = node.get("Type")
        
        # Skip ROOT folder (don't include it in the path)
        if node_name == "ROOT":
            current_path = path_tuple
        else:
            current_path = path_tuple + (node_name,)

        if node_type == "1":  # Playlist
            track_refs = []
            for track_ref in node.findall("TRACK"):
                track_id = track_ref.get("Key")
                if track_id and track_id in tracks:
                    track_info = tracks[track_id]
                    track_refs.append({
                        "id": track_id,
                        "name": track_info["name"],
                        "artist": track_info["artist"],
                        "path": track_info["path"]
                    })
            playlists_data[current_path] = track_refs
        else:  # Folder (Type == "0")
            for child in node.findall("NODE"):
                traverse_nodes(child, current_path)

    playlists_elem = root.find("PLAYLISTS")
    if playlists_elem is not None:
        for node in playlists_elem.findall("NODE"):
            traverse_nodes(node)

    return {"playlists": playlists_data, "tracks": tracks}


def filter_missing_tracks(playlists: dict, xml_path: Path) -> tuple[dict, int, Path]:
    """Remove unavailable files and write a report next to the XML export."""
    available_playlists = {}
    missing_tracks = {}

    for playlist_path, tracks in playlists.items():
        available_tracks = []
        for track in tracks:
            if Path(track["path"]).is_file():
                available_tracks.append(track)
            else:
                missing_tracks.setdefault(track["id"], track)
        available_playlists[playlist_path] = available_tracks

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    report_path = xml_path.parent / f"missing_tracks_{timestamp}.txt"
    with report_path.open("w", encoding="utf-8") as report:
        if missing_tracks:
            report.write("Tracks skipped because their files could not be found:\n\n")
            for track in sorted(
                missing_tracks.values(), key=lambda item: (item["artist"].casefold(), item["name"].casefold())
            ):
                report.write(f'{track["artist"]} - {track["name"]}\n{track["path"]}\n\n')
        else:
            report.write("No missing track files found.\n")

    return available_playlists, len(missing_tracks), report_path


def load_sync_state(xml_path: Path) -> tuple[dict[tuple[str, ...], dict], Path, bool]:
    state_path = xml_path.parent / STATE_FILE_NAME
    if not state_path.exists():
        return {}, state_path, False

    with state_path.open("r", encoding="utf-8") as fh:
        raw_state = json.load(fh)

    playlists = {}
    for item in raw_state.get("playlists", []):
        path = tuple(item.get("path", []))
        if path:
            playlists[path] = {
                "id": item.get("id"),
                "tracks": set(item.get("tracks", [])),
            }
    return playlists, state_path, True


def save_sync_state(xml_path: Path, playlists: dict, playlist_ids: dict[tuple[str, ...], str]) -> Path:
    state_path = xml_path.parent / STATE_FILE_NAME
    state = {
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "playlists": [],
    }
    for path, tracks in sorted(playlists.items()):
        state["playlists"].append(
            {
                "path": list(path),
                "id": playlist_ids.get(path),
                "tracks": sorted(track["path"] for track in tracks),
            }
        )

    with state_path.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return state_path


def read_managed_playlist_ids() -> dict[tuple[str, ...], str]:
    """Read playlist IDs below REKORDBOX without reading every track."""
    script = r'''
    tell application "Music"
        set output to ""
        repeat with p in every user playlist
            try
                set pathParts to {name of p as text}
                set folder1 to parent of p
                if class of folder1 is folder playlist then
                    set beginning of pathParts to name of folder1 as text
                    try
                        set folder2 to parent of folder1
                        if class of folder2 is folder playlist then
                            set beginning of pathParts to name of folder2 as text
                            try
                                set folder3 to parent of folder2
                                if class of folder3 is folder playlist then
                                    set beginning of pathParts to name of folder3 as text
                                    try
                                        set folder4 to parent of folder3
                                        if class of folder4 is folder playlist then
                                            set beginning of pathParts to name of folder4 as text
                                        end if
                                    end try
                                end if
                            end try
                        end if
                    end try
                end if

                set AppleScript's text item delimiters to tab
                set playlistPath to pathParts as text
                set AppleScript's text item delimiters to ""

                if item 1 of pathParts is "REKORDBOX" then
                    set playlistId to persistent ID of p
                    set output to output & playlistId & tab & playlistPath & linefeed
                end if
            end try
        end repeat
        return output
    end tell
    '''
    playlists = {}
    for line in run_apple_script(script).splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        playlist_id = parts[0]
        full_path = tuple(parts[1:])
        if len(full_path) > 1 and full_path[0] == "REKORDBOX":
            playlists[full_path[1:]] = playlist_id
    return playlists


def get_or_create_folder(name: str, parent_id: Optional[str] = None) -> str:
    """Get or create a folder playlist and return its persistent ID."""
    safe_name = escape_for_applescript(name)
    
    if parent_id is None:
        # Root level folder
        script = f'''
        tell application "Music"
            try
                set f to first folder playlist whose name is "{safe_name}"
            on error
                set f to make new folder playlist with properties {{name:"{safe_name}"}}
            end try
            return persistent ID of f as text
        end tell
        '''
    else:
        # Nested folder
        script = f'''
        tell application "Music"
            set parentFolder to first folder playlist whose persistent ID is "{parent_id}"
            try
                set f to first folder playlist of parentFolder whose name is "{safe_name}"
            on error
                set f to make new folder playlist with properties {{name:"{safe_name}"}}
                move f to parentFolder
            end try
            return persistent ID of f as text
        end tell
        '''
    
    return run_apple_script(script)


def create_playlist_in_folder(playlist_name: str, folder_id: Optional[str] = None) -> str:
    """Create a playlist in the specified folder and return its persistent ID."""
    safe_playlist_name = escape_for_applescript(playlist_name)
    
    if folder_id is None:
        # Root level playlist
        script = f'''
        tell application "Music"
            try
                set p to first user playlist whose name is "{safe_playlist_name}"
            on error
                set p to make new playlist with properties {{name:"{safe_playlist_name}"}}
            end try
            return persistent ID of p as text
        end tell
        '''
    else:
        # Playlist in folder
        script = f'''
        tell application "Music"
            set targetFolder to first folder playlist whose persistent ID is "{folder_id}"
            try
                set p to first user playlist of targetFolder whose name is "{safe_playlist_name}"
            on error
                set p to make new playlist with properties {{name:"{safe_playlist_name}"}}
            end try
            try
                if parent of p is not targetFolder then
                    move p to targetFolder
                end if
            on error
                move p to targetFolder
            end try
            return persistent ID of p as text
        end tell
        '''
    
    return run_apple_script(script)


def update_playlist_tracks(playlist_id: str, paths_to_add: set[str], paths_to_remove: set[str]) -> None:
    """Apply file-path changes to one playlist in bounded AppleScript batches."""
    operations = [("add", path) for path in sorted(paths_to_add)]
    operations += [("remove", path) for path in sorted(paths_to_remove)]
    safe_playlist_id = escape_for_applescript(playlist_id)

    for start in range(0, len(operations), 250):
        commands = []
        for operation, path in operations[start : start + 250]:
            escaped_path = escape_for_applescript(path)
            if operation == "add":
                commands.append(
                    f'''            try
                add (POSIX file "{escaped_path}") to targetPlaylist
            on error errMessage
                log "Could not add {escaped_path}: " & errMessage
            end try'''
                )
            else:
                commands.append(
                    f'''            try
                set trackToRemove to (some file track of targetPlaylist whose location is ((POSIX file "{escaped_path}") as alias))
                delete trackToRemove
            on error errMessage
                log "Could not remove {escaped_path}: " & errMessage
            end try'''
                )

        script = f'''tell application "Music"
    set targetPlaylist to first user playlist whose persistent ID is "{safe_playlist_id}"
{chr(10).join(commands)}
end tell
'''
        run_apple_script(script)


def delete_playlist(playlist_id: str) -> None:
    safe_playlist_id = escape_for_applescript(playlist_id)
    run_apple_script(f'''
    tell application "Music"
        delete first user playlist whose persistent ID is "{safe_playlist_id}"
    end tell
    ''')


def main():
    parser = argparse.ArgumentParser(description="Sync Rekordbox playlists to Apple Music with folder structure")
    parser.add_argument("--xml", type=Path, required=True, help="Path to rekordbox_export.xml")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be created without actually creating")
    args = parser.parse_args()

    if not args.xml.exists():
        print(f"{Colors.RED}Error: {args.xml} not found{Colors.END}")
        sys.exit(1)

    print_header("🎵 Rekordbox to Apple Music Sync")

    # Parse XML
    try:
        data = parse_rekordbox_xml(args.xml)
        playlists = data["playlists"]
        print_info(f"Found {len(playlists)} playlists to sync")
        playlists, missing_count, report_path = filter_missing_tracks(playlists, args.xml)
        if missing_count:
            print_warning(f"Skipped {missing_count} missing track file(s); see {report_path}")
        else:
            print_info(f"No missing track files; wrote {report_path}")
    except Exception as e:
        print_error(f"Failed to parse XML: {e}")
        sys.exit(1)

    if not playlists:
        print_warning("No playlists found in XML")
        return

    print_section("Comparing Rekordbox with Apple Music...")
    try:
        current_playlist_ids = read_managed_playlist_ids()
    except Exception as e:
        print_error(f"Failed to read REKORDBOX playlists from Music: {e}")
        sys.exit(1)

    state_playlists, state_path, has_state = load_sync_state(args.xml)
    if not has_state:
        print_warning(
            f"No previous sync state found. Existing REKORDBOX playlists will be trusted on the first live run; state file: {state_path}"
        )

    changes = []
    for path, tracks in sorted(playlists.items()):
        desired_paths = {track["path"] for track in tracks}
        state = state_playlists.pop(path, None)
        playlist_id = current_playlist_ids.get(path) or (state or {}).get("id")

        if state is None and path in current_playlist_ids:
            continue
        if playlist_id is None:
            changes.append(("create", path, desired_paths, set(), None))
            continue

        previous_paths = state["tracks"] if state else set()
        paths_to_add = desired_paths - previous_paths
        paths_to_remove = previous_paths - desired_paths
        if paths_to_add or paths_to_remove:
            changes.append(("update", path, paths_to_add, paths_to_remove, playlist_id))

    orphan_paths = set(state_playlists) | (set(current_playlist_ids) - set(playlists))
    for path in sorted(orphan_paths):
        playlist_id = current_playlist_ids.get(path) or state_playlists.get(path, {}).get("id")
        if playlist_id:
            changes.append(("delete", path, set(), set(), playlist_id))

    print()
    if not changes:
        print_success("Apple Music is already in sync with the Rekordbox XML.")
        if not args.dry_run:
            saved_state = save_sync_state(args.xml, playlists, current_playlist_ids)
            print_info(f"Wrote sync state: {saved_state}")
        return

    heading = "📋 Dry Run - Changes:" if args.dry_run else "Applying changes:"
    print_section(heading)
    for change_type, path, paths_to_add, paths_to_remove, _ in changes:
        display_path = " / ".join(("REKORDBOX",) + path)
        if change_type == "create":
            print_info(f"CREATE {display_path} (+{len(paths_to_add)} tracks)")
        elif change_type == "update":
            print_info(f"UPDATE {display_path} (+{len(paths_to_add)} / -{len(paths_to_remove)} tracks)")
        else:
            print_info(f"DELETE {display_path}")

    if args.dry_run:
        return

    folder_cache: Dict[tuple, str] = {}
    completed = 0
    failed = 0
    for change_type, path, paths_to_add, paths_to_remove, playlist_id in changes:
        display_path = " / ".join(("REKORDBOX",) + path)
        try:
            if change_type == "create":
                folder_id = None
                folder_path = ("REKORDBOX",) + path[:-1]
                for i in range(len(folder_path)):
                    current_folder_path = folder_path[: i + 1]
                    if current_folder_path not in folder_cache:
                        parent_id = folder_cache.get(folder_path[:i])
                        folder_cache[current_folder_path] = get_or_create_folder(
                            current_folder_path[-1], parent_id
                        )
                    folder_id = folder_cache[current_folder_path]
                playlist_id = create_playlist_in_folder(path[-1], folder_id)
                current_playlist_ids[path] = playlist_id
                update_playlist_tracks(playlist_id, paths_to_add, set())
            elif change_type == "update":
                update_playlist_tracks(playlist_id, paths_to_add, paths_to_remove)
            else:
                delete_playlist(playlist_id)
                current_playlist_ids.pop(path, None)
            print_success(f"{change_type.upper()} {display_path}")
            completed += 1
        except Exception as e:
            print_error(f"{change_type.upper()} {display_path}: {str(e)[:100]}")
            failed += 1

    print()
    print_header("✨ Summary")
    print_success(f"Applied {completed} playlist change(s)")
    if failed:
        print_warning(f"Failed to apply {failed} playlist change(s)")
    else:
        saved_state = save_sync_state(args.xml, playlists, current_playlist_ids)
        print_info(f"Wrote sync state: {saved_state}")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n{Colors.YELLOW}Operation cancelled by user.{Colors.END}\n")
        sys.exit(1)
    except Exception as e:
        print(f"\n{Colors.RED}Error:{Colors.END} {e}\n")
        sys.exit(1)
