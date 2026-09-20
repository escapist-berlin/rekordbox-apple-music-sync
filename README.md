# Rekordbox to Apple Music Sync

This script treats a Rekordbox XML export as the source of truth for playlists
inside the `REKORDBOX` folder in Apple Music.

It runs on macOS, uses the Music app through AppleScript, and has no external
Python dependencies. To keep repeated syncs fast, it stores the last successful
sync state next to the XML export.

## Requirements

- macOS with the Music app
- Python 3.10 or newer
- A Rekordbox XML export
- The audio files referenced by the XML export

The script does not modify the Rekordbox XML file.

## Setup

1. In Rekordbox, export your collection as XML.
2. In Music app settings, open **Files** and disable **Copy files to Music
   Media folder when adding to library** if you want Music to reference the
   existing files instead of copying them.
3. Allow Terminal or Python to control Music when macOS asks for Automation
   permission. You can review this in **System Settings → Privacy & Security →
   Automation**.

## Usage

Run the script from the repository directory and provide the path to your XML
file:

```bash
python3 rekordbox_sync.py --xml /path/to/rekordbox_export.xml --dry-run
```

The dry run compares the XML with the last successful sync state and the
playlists currently below `REKORDBOX` in Music. It lists only changes:
playlists to create, tracks to add or remove, and playlists to delete. It does
not change Music.

When the preview looks correct, run the sync without `--dry-run`:

```bash
python3 rekordbox_sync.py --xml /path/to/rekordbox_export.xml
```

For example, if the export is in your Downloads directory:

```bash
python3 rekordbox_sync.py --xml "$HOME/Downloads/rekordbox_export.xml"
```

## What the script creates

The first sync creates a top-level `REKORDBOX` folder in Music and reproduces
the folder hierarchy below it. Each Rekordbox playlist becomes a Music
playlist with the same name.

For example:

```text
Rekordbox:
  HOUSE/
    Warm Up/
      Friday

Music:
  REKORDBOX/
    HOUSE/
      Warm Up/
        Friday
```

Later syncs compare each playlist's file paths to the XML export. Tracks are
added or removed to make the Apple Music playlist match Rekordbox. Large
updates are processed in batches so the AppleScript remains reliable.

The script writes `rekordbox_sync_state.json` beside the XML export after a
successful live sync. This file stores the playlist paths, Music playlist IDs,
and track file paths from the last sync, so future dry runs do not need to read
every track from Music.app.

## Important behavior

- Existing folders and playlists with matching paths are reused.
- Tracks removed from a Rekordbox playlist are removed from its corresponding
  `REKORDBOX` playlist in Music. Library files are never removed.
- A playlist removed from Rekordbox is deleted from the `REKORDBOX` folder in
  Music. Its library tracks are never removed.
- On the first live run after adding sync-state support, existing `REKORDBOX`
  playlists are trusted and recorded in `rekordbox_sync_state.json`. After that,
  future runs show only real differences from the last successful sync.
- Tracks whose files are missing are skipped. Every run writes
  `missing_tracks_YYYY-MM-DD_HH-MM-SS.txt` beside the XML export, listing each
  skipped track and its expected file path. The report says `No missing track
  files found` when every referenced file is available.
- The script adds tracks by file path, so the files must still exist at the
  locations stored in the Rekordbox export.
- The script processes every playlist in the XML export.
- A dry run and a live sync only print playlists with differences. If no
  output appears after the comparison, the `REKORDBOX` playlists already match
  the XML.
- Empty folders left after deleted playlists are not removed automatically.
- Do not delete `rekordbox_sync_state.json` unless you want the next live run to
  re-initialize its idea of the current `REKORDBOX` playlists.

## Troubleshooting

### Music app automation error

Open **System Settings → Privacy & Security → Automation** and allow the
terminal or Python application to control Music. Then run the command again.

### XML file not found

Use an absolute or shell-expanded path and verify the file exists:

```bash
ls -l "$HOME/Downloads/rekordbox_export.xml"
python3 rekordbox_sync.py --xml "$HOME/Downloads/rekordbox_export.xml" --dry-run
```

### Tracks are missing

Check that the files have not been moved since the XML export. Rekordbox stores
the file location in each track entry, and the script skips paths that no
longer exist. Open the newest `missing_tracks_*.txt` file next to the XML
export to see the skipped tracks and their expected paths.

## Files

- `rekordbox_sync.py` — imports the Rekordbox XML and syncs playlists to Music.
- `clear_playlists.py` — utility for clearing playlists when needed.
