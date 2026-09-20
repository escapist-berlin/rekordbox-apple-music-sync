#!/usr/bin/env python3
"""Delete all playlist folders and user playlists from Apple Music.

This does NOT delete music files from the library. It only deletes playlist
objects in the Music app.

Usage:
    python3 clear_playlists.py
"""

import subprocess
import sys


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


def run_apple_script(script: str) -> str:
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


def list_playlist_names(kind: str) -> list[str]:
    """Return the names for all playlists of the given kind."""
    script = f'''
        tell application "Music"
            set output to ""
            repeat with p in every {kind}
                set output to output & (name of p as text) & "\n"
            end repeat
            return output
        end tell
    '''
    output = run_apple_script(script).strip()
    if not output:
        return []
    return [line.strip() for line in output.splitlines() if line.strip()]


def delete_playlist_by_name(kind: str, name: str) -> bool:
    """Delete a single playlist or folder playlist by name.
    
    Returns True if successful, False otherwise.
    """
    safe_name = escape_for_applescript(name)
    script = f'''
        tell application "Music"
            try
                set targetItem to first {kind} whose name is "{safe_name}"
                delete targetItem
                return "OK"
            on error errMsg
                return "ERROR:" & errMsg
            end try
        end tell
    '''
    try:
        result = run_apple_script(script)
        return result == "OK"
    except Exception:
        return False


def clear_all_playlists() -> None:
    print_header("🎵 Apple Music Playlist Cleaner")
    
    print(f"{Colors.YELLOW}⚠  WARNING:{Colors.END}")
    print("  This will delete ALL playlist folders and playlists from Apple Music.")
    print("  It will NOT delete any music files from your library.\n")
    
    answer = input(f"{Colors.BOLD}Type DELETE to continue (or press Enter to cancel):{Colors.END} ")
    
    if answer.strip() != "DELETE":
        print(f"\n{Colors.YELLOW}Cancelled.{Colors.END} No playlists were deleted.\n")
        return

    print()
    
    deleted_count = 0
    failed_count = 0
    
    # Get playlist counts
    user_playlists = list_playlist_names("user playlist")
    folder_playlists = list_playlist_names("folder playlist")
    
    total_count = len(user_playlists) + len(folder_playlists)
    
    print_info(f"Found {len(user_playlists)} user playlists and {len(folder_playlists)} folder playlists")
    print()
    
    # Delete user playlists
    if user_playlists:
        print_section(f"Deleting {len(user_playlists)} user playlists...")
        for i, name in enumerate(user_playlists, 1):
            if delete_playlist_by_name("user playlist", name):
                deleted_count += 1
                # Show progress every 10 items or at the end
                if i % 10 == 0 or i == len(user_playlists):
                    print_info(f"Progress: {i}/{len(user_playlists)}")
            else:
                failed_count += 1
                print_warning(f"Failed to delete: {name}")
        print()

    # Delete folder playlists
    if folder_playlists:
        print_section(f"Deleting {len(folder_playlists)} playlist folders...")
        for i, name in enumerate(folder_playlists, 1):
            if delete_playlist_by_name("folder playlist", name):
                deleted_count += 1
                # Show progress every 10 items or at the end
                if i % 10 == 0 or i == len(folder_playlists):
                    print_info(f"Progress: {i}/{len(folder_playlists)}")
            else:
                failed_count += 1
                print_warning(f"Failed to delete: {name}")
        print()
    
    # Print summary
    print_header("✨ Summary")
    
    print_success(f"Deleted {deleted_count} playlist objects from Apple Music")
    
    if failed_count > 0:
        print_warning(f"Failed to delete {failed_count} items (usually system playlists)")
    
    print_info("Music library files were NOT removed")
    print()


if __name__ == "__main__":
    try:
        clear_all_playlists()
    except KeyboardInterrupt:
        print(f"\n\n{Colors.YELLOW}Operation cancelled by user.{Colors.END}\n")
        sys.exit(1)
    except Exception as e:
        print(f"\n{Colors.RED}Error:{Colors.END} {e}\n")
        sys.exit(1)
