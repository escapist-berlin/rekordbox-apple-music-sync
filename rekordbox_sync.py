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
import subprocess
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Dict


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


def create_playlist_in_folder(playlist_name: str, folder_id: Optional[str] = None) -> None:
    """Create a playlist in the specified folder."""
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
        end tell
        '''
    
    run_apple_script(script)


def add_tracks_to_playlist(playlist_name: str, folder_id: Optional[str], track_list: list) -> int:
    """Add tracks to a playlist using file paths. Returns number of tracks added."""
    if not track_list:
        return 0

    safe_playlist_name = escape_for_applescript(playlist_name)
    total = 0
    for start in range(0, len(track_list), 250):
        add_ops = []
        for track in track_list[start : start + 250]:
            escaped_path = escape_for_applescript(track["path"])
            add_ops.append(
                f'''            try
                add (POSIX file "{escaped_path}") to targetPlaylist
            on error errMessage
                log "Could not add {escaped_path}: " & errMessage
            end try'''
            )

        # Resolve the playlist globally. Looking it up through its folder fails
        # in Music.app with -1728, while this direct lookup works.
        script = f'''tell application "Music"
    set targetPlaylist to first user playlist whose name is "{safe_playlist_name}"
{chr(10).join(add_ops)}
    return count of tracks of targetPlaylist
end tell
'''

        try:
            result = run_apple_script(script)
            total = int(result) if result.isdigit() else total + len(add_ops)
        except Exception as e:
            print_warning(f"Error adding tracks: {e}")
            return total
    return total


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

    print()

    # Dry run summary
    if args.dry_run:
        print_section("📋 Dry Run - What would be created:")
        for path, tracks in sorted(playlists.items()):
            full_path = ("REKORDBOX",) + path
            print_info(f"{' / '.join(full_path)} ({len(tracks)} tracks)")
        print()
        return

    # Create playlists
    print_section("Creating folder structure and playlists...")
    print_info(f"Total playlists to create: {len(playlists)}\n")

    created_count = 0
    failed_count = 0
    total_tracks = 0
    
    # Cache for folder IDs to avoid recreating them
    folder_cache: Dict[tuple, str] = {}

    for idx, (path, tracks) in enumerate(sorted(playlists.items()), 1):
        full_path = ("REKORDBOX",) + path
        playlist_name = full_path[-1]
        folder_path = full_path[:-1]

        try:
            # Create folder structure (using cache to avoid duplication)
            folder_id = None
            if len(folder_path) > 0:
                # Build folder path step by step
                for i in range(len(folder_path)):
                    current_folder_path = folder_path[:i+1]
                    if current_folder_path not in folder_cache:
                        parent_id = folder_cache.get(folder_path[:i])
                        folder_name = current_folder_path[-1]
                        folder_id = get_or_create_folder(folder_name, parent_id)
                        folder_cache[current_folder_path] = folder_id
                    else:
                        folder_id = folder_cache[current_folder_path]

            # Create playlist
            create_playlist_in_folder(playlist_name, folder_id)

            # Add tracks
            track_count = add_tracks_to_playlist(playlist_name, folder_id, tracks)
            total_tracks += track_count

            display_path = " / ".join(full_path)
            print_success(f"[{idx}/{len(playlists)}] {display_path} ({track_count}/{len(tracks)} tracks)")
            created_count += 1
            
            # Show progress every 20 playlists
            if idx % 20 == 0:
                print_info(f"Progress: {created_count} created, {failed_count} failed")

        except Exception as e:
            display_path = " / ".join(full_path)
            print_error(f"[{idx}/{len(playlists)}] {display_path}: {str(e)[:60]}")
            failed_count += 1

    print()
    print_header("✨ Summary")
    print_success(f"Created {created_count} playlists in REKORDBOX folder")
    if failed_count > 0:
        print_warning(f"Failed to create {failed_count} playlists")
    print_info(f"Added {total_tracks} tracks to playlists")
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
