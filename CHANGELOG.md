# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-10-27

### Added

- **Exclusions for false-positive duplicates.** Mark results as "not a duplicate" so future scans skip them:
  - **Exclude group** — hides a whole duplicate group (a movie, or a single TV episode). The group stays hidden only while it contains the same files it had when excluded; if a new copy appears later, the group is reported again.
  - **Exclude file** — a per-file button that leaves that file (matched by path) out of all future scans. If fewer than two files remain, the group disappears.
  - Excluding updates the current results immediately, with no re-scan needed; open groups stay open.
- **Exclusions manager.** A new **⊘ Exclusions** header button opens a modal with Movies / TV Series tabs listing excluded groups and files with the date each was added. Remove a single entry, or use **Remove All** for the current tab.
- **Search in the Exclusions manager.** Filter long exclusion lists by title or file path. Search ignores case and accents (e.g. `amelie` finds *Amélie*), every word must match, and section headers show how many entries match (e.g. *2 of 43*). While a search is active, **Remove All** becomes **Remove Shown (N)** and removes only the matching entries.
- **"Exclusions Applied" stat** on the results page shows how many exclusions hid something in the last scan. Click it to open the Exclusions manager.
- Translations (EN / PL / DE) for all new UI.

### Changed

- Exclusions are stored in a new file, `data/exclusions.json`, separate from `config.json`, so they can be backed up or reset without touching connection settings or the API key. It lives in the same Docker volume and persists across restarts and upgrades.
- After deleting items from Emby, the results list is rebuilt from the remaining data: duplicate counts update and open groups stay open.
- Switching language while results or the Exclusions modal are on screen now re-renders them in the new language.

### Fixed

- **TV series selection:** ticking an episode checkbox threw a JavaScript error, and the "Select all in group" / "Clear group" links did nothing for episodes. The "N selected" badge on each series now updates correctly.
- **"Clear group" left files queued for deletion:** cleared files still appeared in the delete confirmation and could be deleted. Clearing a group now removes them from the delete list too.
- **Delete in TV results:** after deleting, episodes left with a single file (and series with no episodes left) stayed on screen until the next scan. They are now removed.
- **Failed deletes disappeared from the list:** files were removed from the results even when Emby refused to delete them. Only successfully deleted items are removed now; failed ones stay listed and selected so you can retry.
- **Multi-version items:** deleting an Emby item that has several versions now removes all of its files from the results, not just the ticked one.
- The "N selected" badge is now translated.

## [1.0.0] - 2026-04-22

- Initial release.

[1.1.0]: https://github.com/BoskiAlvaro/Emby-Dupe-Detective/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/BoskiAlvaro/Emby-Dupe-Detective/releases/tag/v1.0.0
