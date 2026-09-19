#!/usr/bin/env python3
"""
sync.py - Rekordbox -> Apple Music one-way sync (v1.2)

Reads a Rekordbox collection XML export and mirrors selected playlists --
including their original folder structure -- as FLAT playlists in Apple
Music (Music.app). Because Music.app does not reliably support creating
(nested) folder playlists via AppleScript (see the comment near
`encode_playlist_name` below), the original Rekordbox path is instead
encoded into the playlist name (e.g. "Warm Up Sets / Deep House").

Example: A Rekordbox playlist "Deep House" inside the folder "Warm Up Sets"
ends up in Apple Music as a flat playlist named:
    Warm Up Sets / Deep House

Important:
- Rekordbox is NEVER written to. This script only reads the XML file.
- Sync is strictly one-directional: Rekordbox -> Apple Music.
- All playlists created will have their Rekordbox path encoded in the name.
- Audio files are never copied. Prerequisite: in Music.app under
  Settings > Files, "Copy files to Music Media folder when adding to
  library" must be DISABLED. This script cannot/does not toggle that
  setting itself.

Usage:
    python3 sync.py --xml rekordbox_export.xml --all --dry-run
    python3 sync.py --xml rekordbox_export.xml --all

    # individual playlists (by name if unique, otherwise by full path):
    python3 sync.py --xml rekordbox_export.xml \
        --playlists "Techno Prime,Warm Up Sets/Deep House"

Only needs the standard library (no pip install required).
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import subprocess
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

DEFAULT_MIRROR_FOLDER = "REKORDBOX MIRROR"


# --------------------------------------------------------------------------
# Data models
# --------------------------------------------------------------------------

@dataclasses.dataclass
class RekordboxTrack:
    track_id: str
    name: str
    location: Path  # absolute, decoded local file path


@dataclasses.dataclass
class SyncPlan:
    path: tuple[str, ...]     # e.g. ("Warm Up Sets", "Deep House") -- last element is the playlist name
    desired_paths: set[str]   # desired state (from Rekordbox), as str(Path)
    missing_files: list[str]  # paths that should exist per the XML but are missing

    @property
    def leaf_name(self) -> str:
        return self.path[-1]

    @property
    def folder_names(self) -> tuple[str, ...]:
        return self.path[:-1]

    @property
    def display_name(self) -> str:
        return " / ".join(self.path)


@dataclasses.dataclass
class PlaylistDiff:
    path: tuple[str, ...]
    to_add: set[str]
    to_remove: set[str]
    unchanged_count: int

    @property
    def display_name(self) -> str:
        return " / ".join(self.path)


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

class Logger:
    def __init__(self, log_dir: Path):
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.log_path = log_dir / f"sync_{timestamp}.log"
        self._fh = open(self.log_path, "w", encoding="utf-8")
        self.errors: list[str] = []

    def info(self, msg: str) -> None:
        print(msg)
        self._fh.write(msg + "\n")

    def detail(self, msg: str) -> None:
        """Like info(), but only goes to the log file, not the terminal.
        For detail lines (individual tracks/files) that would make the
        terminal output unreadable for large libraries."""
        self._fh.write(msg + "\n")

    def error(self, msg: str, console: bool = False) -> None:
        full = f"ERROR: {msg}"
        if console:
            print(full, file=sys.stderr)
        self._fh.write(full + "\n")
        self.errors.append(msg)

    def progress(self, msg: str) -> None:
        """Overwrites the current terminal line (for progress indicators
        during long-running batch operations)."""
        print(f"\r{msg}", end="", flush=True)

    def progress_done(self) -> None:
        print()

    def close(self) -> None:
        self._fh.close()


# --------------------------------------------------------------------------
# Step 1: Read the Rekordbox XML (including folder structure)
# --------------------------------------------------------------------------

def decode_rekordbox_location(location: str) -> Path:
    """Rekordbox stores paths as file://localhost/... URIs (URL-encoded)."""
    parsed = urllib.parse.urlparse(location)
    raw_path = urllib.parse.unquote(parsed.path)
    return Path(raw_path)


def parse_rekordbox_xml(
    xml_path: Path,
) -> tuple[dict[str, RekordboxTrack], dict[tuple[str, ...], list[str]]]:
    """
    Returns:
      - tracks: TrackID -> RekordboxTrack
      - playlists: full path (folder..., playlist name) -> list of TrackIDs

    The complete Rekordbox folder tree is represented as a path tuple, e.g.
    ("Warm Up Sets", "Deep House"). The virtual root node "ROOT" that
    Rekordbox creates itself is skipped (it does not appear as its own
    folder).
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    collection = root.find("COLLECTION")
    if collection is None:
        raise ValueError("No <COLLECTION> element found. Is this a valid Rekordbox export?")

    tracks: dict[str, RekordboxTrack] = {}
    for track_elem in collection.findall("TRACK"):
        track_id = track_elem.get("TrackID")
        location = track_elem.get("Location")
        name = track_elem.get("Name", "")
        if track_id is None or location is None:
            continue
        tracks[track_id] = RekordboxTrack(
            track_id=track_id, name=name, location=decode_rekordbox_location(location)
        )

    playlists_root = root.find("PLAYLISTS")
    if playlists_root is None:
        raise ValueError("No <PLAYLISTS> element found.")

    playlists: dict[tuple[str, ...], list[str]] = {}

    def walk(node: ET.Element, path: tuple[str, ...]) -> None:
        for child in node.findall("NODE"):
            name = child.get("Name", "")
            if child.get("Type") == "1":
                track_ids = [t.get("Key") for t in child.findall("TRACK") if t.get("Key")]
                playlists[path + (name,)] = track_ids
            else:
                # don't carry the root folder "ROOT" along as a folder name of its own
                if name.upper() == "ROOT" and path == ():
                    walk(child, path)
                else:
                    walk(child, path + (name,))

    walk(playlists_root, ())
    return tracks, playlists


# --------------------------------------------------------------------------
# Step 2: Select playlists + compute desired state
# --------------------------------------------------------------------------

def resolve_playlist_selection(
    playlists: dict[tuple[str, ...], list[str]],
    wanted: list[str] | None,
) -> list[tuple[str, ...]]:
    if wanted is None:
        return list(playlists.keys())

    by_leaf_name: dict[str, list[tuple[str, ...]]] = {}
    for path in playlists:
        by_leaf_name.setdefault(path[-1], []).append(path)

    selected: list[tuple[str, ...]] = []
    for entry in wanted:
        entry = entry.strip()
        if "/" in entry:
            candidate = tuple(part.strip() for part in entry.split("/") if part.strip())
            if candidate not in playlists:
                raise ValueError(f"Playlist path not found: '{entry}'")
            selected.append(candidate)
        else:
            matches = by_leaf_name.get(entry, [])
            if not matches:
                raise ValueError(f"Playlist not found: '{entry}'")
            if len(matches) > 1:
                options = "; ".join(" / ".join(m) for m in matches)
                raise ValueError(
                    f"Playlist name '{entry}' is ambiguous. Please provide the full path, e.g.: {options}"
                )
            selected.append(matches[0])
    return selected


def build_sync_plans(
    tracks: dict[str, RekordboxTrack],
    playlists: dict[tuple[str, ...], list[str]],
    wanted: list[str] | None,
) -> list[SyncPlan]:
    selected_paths = resolve_playlist_selection(playlists, wanted)
    plans: list[SyncPlan] = []
    for path in selected_paths:
        desired_paths: set[str] = set()
        missing_files: list[str] = []
        for track_id in playlists[path]:
            track = tracks.get(track_id)
            if track is None:
                continue
            if not track.location.exists():
                missing_files.append(str(track.location))
                continue
            desired_paths.add(str(track.location))
        plans.append(SyncPlan(path=path, desired_paths=desired_paths, missing_files=missing_files))
    return plans


# --------------------------------------------------------------------------
# AppleScript helpers: reference to a flat playlist with the Rekordbox
# path encoded in its name
# --------------------------------------------------------------------------
#
# Music.app 1.5.6 / macOS 15.7.3 does not reliably support creating
# playlists INSIDE a folder (folder playlist) via AppleScript -- this was
# tested extensively with every documented/community-suggested syntax
# variant (make new ... at end of playlists of X, tell X to make new ...,
# ... with properties {parent: X}, ... with properties {container: X},
# reveal X + make new without a target, and the "move <object> to <target>"
# pattern used by community reference implementations): none of them
# actually nests the new object inside the target folder -- even a single
# level of nesting (a playlist directly inside one folder, no
# sub-folders) fails (error "-10014: a routine can only be run in
# response to certain events", or the playlist silently ends up at the
# top level instead of inside the folder). This is a Music.app bug on
# Apple's side, not a Python issue.
#
# Fallback: NO real folder is created in Apple Music. Instead, all
# playlists sit flat at the top level of the Music library, and their name
# encodes the full Rekordbox folder path, separated by " / ", e.g.:
#   Rekordbox: Warm Up Sets > Deep House
#   Apple Music (flat playlist): "Warm Up Sets / Deep House"
# Because Apple Music sorts playlists alphabetically, all Mirror playlists
# still end up visibly clustered together.

PATH_SEPARATOR = " / "


def encode_playlist_name(mirror_folder: str, path: tuple[str, ...]) -> str:
    """Encodes the Rekordbox path into a single flat Apple Music playlist name
    (without the mirror folder prefix). Example: "Warm Up Sets / Deep House"."""
    return PATH_SEPARATOR.join(path)


def escape_applescript_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def playlist_reference(mirror_folder: str, path: tuple[str, ...]) -> str:
    esc_name = escape_applescript_string(encode_playlist_name(mirror_folder, path))
    return f'playlist "{esc_name}"'




# --------------------------------------------------------------------------
# Step 3: Read the current state from Apple Music
# --------------------------------------------------------------------------

# Music.app/osascript crashes or aborts with a cryptic "-1750" error when
# a single AppleScript grows too large or resolves too many
# playlist/track references within one "tell" block (reproducible in
# testing at around 154,000 characters of script length, or roughly
# 330-340 playlists in a single call -- so large libraries are processed
# via multiple smaller osascript calls).
MAX_SCRIPT_CHARS = 90_000
MAX_TRACK_OPS_PER_CHUNK = 250


def run_osascript(script_text: str) -> str:
    result = subprocess.run(["osascript", "-"], input=script_text, capture_output=True, text=True)
    if result.returncode != 0:
        stderr = result.stderr.strip() or "(no error message -- osascript probably crashed/segfaulted)"
        raise RuntimeError(
            f"AppleScript error (script length: {len(script_text)} chars):\n{stderr}"
        )
    return result.stdout


def chunk_blocks(blocks: list[str], max_chars: int = MAX_SCRIPT_CHARS) -> list[list[str]]:
    """Groups script blocks so each group stays under max_chars (a single
    block that is too large on its own ends up alone in its own group)."""
    batches: list[list[str]] = []
    current: list[str] = []
    current_len = 0
    for block in blocks:
        block_len = len(block)
        if current and current_len + block_len > max_chars:
            batches.append(current)
            current = []
            current_len = 0
        current.append(block)
        current_len += block_len
    if current:
        batches.append(current)
    return batches


READ_LIBRARY_TEMPLATE = """
tell application "Music"
    set outputLines to {}

    set allLocations to {}
    try
        set allLocations to (location of every file track of library playlist 1)
    end try
    repeat with locItem in allLocations
        try
            set posixLoc to POSIX path of (locItem as alias)
            set end of outputLines to "LIBTRACK" & tab & posixLoc
        end try
    end repeat

    set AppleScript's text item delimiters to linefeed
    set outputText to outputLines as text
    set AppleScript's text item delimiters to ""
    return outputText
end tell
"""

READ_STATE_TEMPLATE = """
tell application "Music"
    set outputLines to {{}}

{playlist_blocks}

    set AppleScript's text item delimiters to linefeed
    set outputText to outputLines as text
    set AppleScript's text item delimiters to ""
    return outputText
end tell
"""

READ_PLAYLIST_BLOCK_TEMPLATE = """        try
            set plLocations to (location of every file track of {playlist_ref})
            repeat with locItem in plLocations
                try
                    set posixLoc to POSIX path of (locItem as alias)
                    set end of outputLines to "PLAYLIST_TRACK" & tab & "{index}" & tab & posixLoc
                end try
            end repeat
        end try
"""


def read_music_state(
    mirror_folder: str, plans: list[SyncPlan], logger: "Logger | None" = None
) -> tuple[set[str], dict[int, set[str]]]:
    """
    Reads the current state from Music.app.
    Missing playlists result in an empty result for that plan (they will
    be created from scratch when applying) -- not an error.

    To avoid osascript crashes/errors on very large libraries, the library
    is read once separately, and playlists are processed in several
    smaller batches (instead of one giant script).
    """
    library_paths: set[str] = set()
    current_by_index: dict[int, set[str]] = {i: set() for i in range(len(plans))}

    lib_output = run_osascript(READ_LIBRARY_TEMPLATE)
    for line in lib_output.splitlines():
        if line.startswith("LIBTRACK\t"):
            library_paths.add(line.split("\t", 1)[1])

    blocks = [
        READ_PLAYLIST_BLOCK_TEMPLATE.format(playlist_ref=playlist_reference(mirror_folder, plan.path), index=idx)
        for idx, plan in enumerate(plans)
    ]
    batches = chunk_blocks(blocks)

    for batch_no, batch in enumerate(batches, start=1):
        if logger:
            logger.progress(f"Reading playlist contents from Apple Music ... batch {batch_no}/{len(batches)}")
        script = READ_STATE_TEMPLATE.format(playlist_blocks="".join(batch))
        output = run_osascript(script)
        _parse_read_output(output, current_by_index)
    if logger and batches:
        logger.progress_done()

    return library_paths, current_by_index


def _parse_read_output(output: str, current_by_index: dict[int, set[str]]) -> None:
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        tag = parts[0]
        if tag == "PLAYLIST_TRACK" and len(parts) >= 3:
            idx = int(parts[1])
            current_by_index.setdefault(idx, set()).add(parts[2])


# --------------------------------------------------------------------------
# Step 4: Compute diffs (pure Python logic)
# --------------------------------------------------------------------------

def compute_diffs(plans: list[SyncPlan], current_by_index: dict[int, set[str]]) -> list[PlaylistDiff]:
    diffs = []
    for idx, plan in enumerate(plans):
        current = current_by_index.get(idx, set())
        to_add = plan.desired_paths - current
        to_remove = current - plan.desired_paths
        unchanged = len(plan.desired_paths & current)
        diffs.append(
            PlaylistDiff(path=plan.path, to_add=to_add, to_remove=to_remove, unchanged_count=unchanged)
        )
    return diffs


# --------------------------------------------------------------------------
# Step 5: Apply changes to Apple Music
# --------------------------------------------------------------------------

APPLY_TEMPLATE = """
tell application "Music"
{playlist_blocks}
end tell
"""

ADD_TRACK = """    try
        add (POSIX file "{path}") to thePlaylist
    on error errMsg
        log "Could not add file: {path} -- " & errMsg
    end try
"""

REMOVE_TRACK = """    try
        set targetAlias to (POSIX file "{path}") as alias
        set trackToRemove to (some file track of thePlaylist whose location is targetAlias)
        delete trackToRemove
    on error errMsg
        log "Could not remove track: {path} -- " & errMsg
    end try
"""


def build_playlist_header(mirror_folder: str, diff: PlaylistDiff) -> str:
    playlist_ref = playlist_reference(mirror_folder, diff.path)
    esc_name = escape_applescript_string(encode_playlist_name(mirror_folder, diff.path))
    return (
        f'    if not (exists {playlist_ref}) then\n'
        f'        make new playlist with properties {{name:"{esc_name}"}}\n'
        f"    end if\n"
        f"    set thePlaylist to {playlist_ref}\n\n"
    )


def build_track_op_lines(diff: PlaylistDiff) -> list[str]:
    """`add` adds a file to the playlist by path. If the file is already
    part of the Music library (referenced, not copied), it is simply
    referenced again rather than duplicated -- a separate "duplicate
    existing track" approach is not needed and was error-prone in earlier
    versions (Music.app: "-10014: a routine can only be run in response
    to certain events", because "some ... whose location is (POSIX file
    ...)" is not reliable in Music.app)."""
    op_lines = [ADD_TRACK.format(path=escape_applescript_string(path)) for path in sorted(diff.to_add)]
    op_lines += [REMOVE_TRACK.format(path=escape_applescript_string(path)) for path in sorted(diff.to_remove)]
    return op_lines


def build_playlist_apply_chunks(mirror_folder: str, diff: PlaylistDiff) -> list[str]:
    """Builds one or more self-contained script blocks for a playlist.
    Very large playlists (many tracks to add/remove) are split across
    several blocks (= separate later osascript calls) so no single
    AppleScript grows too large. The header (create the playlist if
    needed) is idempotent and repeated for each block."""
    header = build_playlist_header(mirror_folder, diff)
    op_lines = build_track_op_lines(diff)

    blocks = []
    for i in range(0, len(op_lines), MAX_TRACK_OPS_PER_CHUNK):
        chunk_ops = op_lines[i : i + MAX_TRACK_OPS_PER_CHUNK]
        blocks.append(header + "".join(chunk_ops))
    return blocks


def build_apply_scripts(mirror_folder: str, diffs: list[PlaylistDiff]) -> list[str]:
    """Returns a list of complete AppleScript scripts (instead of one
    single giant script), so osascript doesn't crash on large libraries
    (see MAX_SCRIPT_CHARS)."""
    all_blocks = []
    for diff in diffs:
        if diff.to_add or diff.to_remove:
            all_blocks.extend(build_playlist_apply_chunks(mirror_folder, diff))
    if not all_blocks:
        return []

    batches = chunk_blocks(all_blocks)
    return [
        APPLY_TEMPLATE.format(playlist_blocks="\n".join(batch))
        for batch in batches
    ]


# --------------------------------------------------------------------------
# Main program
# --------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rekordbox -> Apple Music one-way sync (v1.2, manual run)")
    parser.add_argument("--xml", required=True, type=Path, help="Path to the exported Rekordbox collection XML")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--playlists",
        type=str,
        help="Comma-separated list of playlist names or full paths (e.g. 'Techno Prime,Warm Up Sets/Deep House')",
    )
    group.add_argument("--all", action="store_true", help="Sync all playlists from the XML (including folder structure)")
    parser.add_argument(
        "--mirror-folder",
        type=str,
        default=DEFAULT_MIRROR_FOLDER,
        help=f"(Deprecated: no longer used) Mirror folder prefix. Kept for backward compatibility.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Only show what would happen -- change nothing")
    parser.add_argument("--log-dir", type=Path, default=Path("logs"), help="Directory for log files")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logger = Logger(args.log_dir)

    try:
        logger.info(f"=== Rekordbox -> Apple Music sync started: {datetime.datetime.now()} ===")
        logger.info(f"XML file: {args.xml}")

        if not args.xml.exists():
            logger.error(f"XML file not found: {args.xml}", console=True)
            return 1

        tracks, playlists = parse_rekordbox_xml(args.xml)
        logger.info(f"Found {len(tracks)} tracks and {len(playlists)} playlists (including sub-folders) in the XML.")

        wanted = None if args.all else [p for p in args.playlists.split(",") if p.strip()]

        try:
            plans = build_sync_plans(tracks, playlists, wanted)
        except ValueError as e:
            logger.error(str(e), console=True)
            return 1

        total_missing = 0
        for plan in plans:
            logger.detail(f"Playlist '{plan.display_name}': {len(plan.desired_paths)} existing tracks (desired state)")
            for missing in plan.missing_files:
                logger.error(f"File missing (playlist '{plan.display_name}'): {missing}")
            total_missing += len(plan.missing_files)
        logger.info(f"{len(plans)} playlist(s) selected (see log file for details).")
        if total_missing:
            logger.info(f"{total_missing} file(s) referenced in the XML are missing locally (see log file for details).")

        logger.info("Reading current state from Apple Music ...")
        library_paths, current_by_index = read_music_state(args.mirror_folder, plans, logger=logger)
        logger.info(f"Found {len(library_paths)} tracks currently in the Apple Music library.")

        diffs = compute_diffs(plans, current_by_index)

        logger.info("")
        logger.info("=== Planned changes ===")
        any_changes = False
        changed_playlists = 0
        total_to_add = 0
        total_to_remove = 0
        for diff in diffs:
            has_changes = bool(diff.to_add or diff.to_remove)
            summary = (
                f"Playlist '{diff.display_name}': "
                f"+{len(diff.to_add)} to add, -{len(diff.to_remove)} to remove, "
                f"{diff.unchanged_count} unchanged"
            )
            if has_changes:
                any_changes = True
                changed_playlists += 1
                total_to_add += len(diff.to_add)
                total_to_remove += len(diff.to_remove)
                logger.info(summary)
            else:
                logger.detail(summary)
            for p in sorted(diff.to_add):
                logger.detail(f"    + {p}")
            for p in sorted(diff.to_remove):
                logger.detail(f"    - {p}")

        if not any_changes:
            logger.info("No changes needed. Everything is already in sync.")
        else:
            logger.info(
                f"Summary: {changed_playlists} playlist(s) with changes, "
                f"total +{total_to_add} / -{total_to_remove} tracks "
                f"(see log file for individual tracks)."
            )

        if args.dry_run:
            logger.info("")
            logger.info("[DRY-RUN] No changes were made to Apple Music.")
        elif any_changes:
            logger.info("")
            logger.info("Applying changes to Apple Music ...")
            apply_scripts = build_apply_scripts(args.mirror_folder, diffs)
            for batch_no, script in enumerate(apply_scripts, start=1):
                logger.progress(f"Applying changes ... batch {batch_no}/{len(apply_scripts)}")
                run_osascript(script)
            if apply_scripts:
                logger.progress_done()
            logger.info("Changes applied.")

        logger.info("")
        if logger.errors:
            logger.info(f"=== Done with {len(logger.errors)} error(s)/warning(s). Log: {logger.log_path} ===")
        else:
            logger.info(f"=== Done, no errors. Log: {logger.log_path} ===")

        return 0 if not logger.errors else 2

    finally:
        logger.close()


if __name__ == "__main__":
    sys.exit(main())
