# Rekordbox to Apple Music Sync

This script reads a Rekordbox XML export and recreates all Rekordbox playlists
in Apple Music, including their folder structure and tracks.

It runs on macOS, uses the Music app through AppleScript, and has no external
Python dependencies.

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

The dry run parses the XML and lists the playlists and track counts without
changing Music.

When the preview looks correct, run the sync without `--dry-run`:

```bash
python3 rekordbox_sync.py --xml /path/to/rekordbox_export.xml
```

For example, if the export is in your Downloads directory:

```bash
python3 rekordbox_sync.py --xml "$HOME/Downloads/rekordbox_export.xml"
```

## What the script creates

The script creates a top-level `REKORDBOX` folder in Music and reproduces the
folder hierarchy below it. Each Rekordbox playlist becomes a Music playlist
with the same name.

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

Tracks are added to each playlist from the file paths in the XML export. Large
playlists are processed in batches so the AppleScript remains reliable.

## Important behavior

- Existing folders and playlists with matching names are reused.
- Existing tracks are not removed from Music playlists.
- Tracks whose files are missing are skipped.
- The script adds tracks by file path, so the files must still exist at the
  locations stored in the Rekordbox export.
- If a playlist name is repeated in different Rekordbox folders, Music app
  playlist lookup by name may be ambiguous. Keeping playlist names unique is
  recommended.
- The script processes every playlist in the XML export.
- Running the script again is safe for the folder and playlist creation step;
  Music may ignore tracks that are already present.

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
longer exist.

## Files

- `rekordbox_sync.py` — imports the Rekordbox XML and syncs playlists to Music.
- `clear_playlists.py` — utility for clearing playlists when needed.
