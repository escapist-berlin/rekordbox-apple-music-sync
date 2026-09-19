# Rekordbox → Apple Music Sync (v1.1)

Single Python script, no external dependencies (standard library only).
Requires macOS with Music.app and Python 3.10+ (check with `python3 --version`).

The full Rekordbox folder structure is reproduced — however not as real,
nested Apple Music folders (see the "Known, permanent limitation" section
below), but as **flat playlists with the path encoded in the name**: a
playlist "Deep House" inside the Rekordbox folder "Warm Up Sets" ends up in
Apple Music as a flat playlist named:

    Warm Up Sets / Deep House

Since Apple Music sorts playlists alphabetically, related playlists visibly
cluster together as a result.

## One-time setup

1. In **Music.app → Preferences → Files**: uncheck *"Copy files to music
   library when adding to library"* (or *"Keep music media folder
   organized"*). This is required so audio files are only referenced and
   not duplicated. This script cannot and should not change this setting
   itself.
2. In **System Settings → Privacy & Security → Automation**: on first run,
   macOS will ask whether your Terminal/Python may control Music.app – you
   must allow this. Without this permission, every call will fail with an
   AppleScript permission error.
3. Check the Python version: `python3 --version` should be 3.10 or newer.

## Usage

Prerequisite: `sync.py` and the exported `rekordbox_export.xml` are in the
same folder (in this example `~/Downloads`).

```bash
# 0. In Rekordbox: File → Export Collection as XML
#    → save in the same folder as sync.py, e.g. as rekordbox_export.xml

cd /Users/deniskolokolov/Downloads

# 1. First do a dry run -- shows all planned changes without changing anything
python3 sync.py --xml rekordbox_export.xml --all --dry-run

# 2. If the preview looks right: apply for real
python3 sync.py --xml rekordbox_export.xml --all
```

To sync only specific playlists instead of `--all`:

```bash
# by name (only if the name is unique across the entire Rekordbox collection)
python3 sync.py --xml rekordbox_export.xml --playlists "Techno Prime" --dry-run

# by full path, if the name is ambiguous or you specifically mean a
# subfolder (folder and playlist name separated by "/")
python3 sync.py --xml rekordbox_export.xml \
                 --playlists "Techno Prime,Warm Up Sets/Deep House" --dry-run
```

Options:
- `--log-dir logs` – where log files are written (default: `./logs`, relative to the current directory)

**Recommended workflow for the very first live run:** don't immediately run
`--all` against the entire library; first restrict to a small test playlist
(2–3 tracks) using `--playlists`, check the result in Apple Music, and only
then expand to `--all`.

## Terminal output

The terminal only shows a short summary (number of playlists, changed
playlists, total added/removed counts, progress bar for batch processing).
All details (every individual playlist, every individual track, every
missing file) are logged exclusively to the log file under
`logs/sync_<timestamp>.log` – check there if you need details.

## Known, permanent limitation: no real nested folders

**Status: thoroughly tested, September 2026.** On Music.app 1.5.6 /
macOS 15.7.3, creating nested folder playlists (`folder playlist`) via
AppleScript **cannot** be reliably implemented. This was tested with all
known/documented syntax variants, including:

- `make new playlist/folder playlist ... at end of playlists of X`
- `tell X to make new playlist ...`
- `... with properties {parent: X}`
- `... with properties {container: X}`
- `reveal X` (select in the UI) + `make new folder playlist` without a target
- `make new user playlist at X with properties {...}` (community syntax)
- Creating the object without a target and then moving it into the target
  folder afterwards via `move childObject to parentObject` (a technique
  from a community reference project for Music.app AppleScript automation)

**Result in all cases:** the command either fails with error "-10014"
("The routine only handles single objects") without doing anything, or it
reports success without the object actually ending up in the target folder
(verified multiple times via `exists childX of parentY`,
`count of (playlists of parentY)`, and `first folder playlist of parentY
whose name is ...` – all three checks consistently confirm: no real
nesting). This already affects the simplest case (a single playlist
directly inside a folder, no subfolders) – deeper nesting wasn't even
attempted since already the first level doesn't work.

This is a bug/limitation on Apple's side in this Music.app version, not a
Python or script issue. That's why this script does **not** create real
folders in Apple Music. Instead, the full Rekordbox path is encoded into
the playlist name (see above, "Warm Up Sets / Deep House") – this fallback
is final and in production use. If you have a newer/older Music.app version
where nesting works, feel free to let me know – then detection logic could
be added to use real folders on systems where it works.

## Known limitations of v1.1

- **Deleted/deselected playlists are not automatically removed.** If you
  remove a playlist from `--playlists` or delete it in Rekordbox, the
  corresponding Apple Music playlist remains unchanged (it just stops
  being updated). Manually deleting the flat playlist in Apple Music is
  still necessary in v1.1.
- **No real nested folders** – see section above. Playlists are flat with
  the path encoded in the name.
- **Playlist names in `--playlists` without "/":** If a name is used that
  is identical across multiple Rekordbox folders, the script aborts with
  an error and lists the possible full paths – in that case simply provide
  the full path (`Folder/Subfolder/PlaylistName`). This issue does not
  occur with `--all`, since the full folder structure is used there anyway.
- **Performance with very large libraries:** Reading library paths happens
  once per run as a bulk query (not per track). With ~8000 tracks / ~380
  playlists, a full dry run currently takes about 15–20 seconds.

## Next steps

- Automatic removal of orphaned mirror playlists
- Automating the sync run (e.g. via `launchd`)
- Possibly switching to PyXA instead of raw AppleScript strings, if
  maintainability justifies it later
