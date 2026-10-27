# Emby Dupe Finder

A web application for finding and managing duplicate movies in your Emby media server library. Scans your entire movie collection, identifies duplicates using provider IDs (IMDb/TMDb) or title+year matching, and scores each file to help you decide which copies to keep.

<img width="1451" height="752" alt="emby-dupe-detective" src="https://github.com/user-attachments/assets/c65c5f6a-1bf9-4af5-8da2-ac3785aa8b31" />

## Features

- **Duplicate Detection** - scans your Emby library and groups duplicate movies by IMDb/TMDb ID (falls back to title + year matching)
- **Quality Scoring** - automatically scores each file based on video/audio codecs, resolution, bitrate, dimensions, audio channels, file size, and filename patterns
- **Configurable Scoring** - fully customizable scoring weights via an in-app modal (codec scores, resolution scores, filename pattern bonuses/penalties)
- **Auto-Select Losers** - optional setting to automatically select all lower-scored duplicates for removal
- **Detailed File Info** - view path, file size, resolution, video codec & bitrate, audio tracks (codec, channels, bitrate, language), and container format
- **Export Paths** - export selected file paths to a `.txt` file for use with your preferred deletion tool
- **Delete via API** - optionally delete selected items directly through the Emby API
- **Path Mapping** - configure export path transformations (prefix, suffix, path replacements) for different OS/mount configurations
- **Exclusions** - mark false positives as "not a duplicate" (a whole group or a single file) so future scans skip them; manage or undo them from the Exclusions modal
- **Persistent Configuration** - all settings saved to `config.json` and persist across restarts
- **Dark Theme UI** - modern, responsive single-page application

## Quick Start

### Docker Compose (recommended)

```bash
git clone <repo-url>
cd emby-dupe-finder
docker compose up -d
```

Open **http://localhost:5000** in your browser.

### Docker

```bash
docker build -t emby-dupe-finder .
docker run -d \
  --name emby-dupe-finder \
  -p 5000:5000 \
  -v emby_dupes_data:/app/data \
  emby-dupe-finder
```

### Local Development

```bash
pip install -r requirements.txt
python app.py
```

The app runs at **http://localhost:5000** by default in local mode.

## Docker Compose Configuration

The included `docker-compose.yml` provides a production-ready setup:

```yaml
services:
  emby-dupe-detective:
    build: .
    container_name: emby-dupe-detective
    ports:
      - "5000:5000"
    volumes:
      - emby_dupes_data:/app/data
    environment:
      - PORT=5000
      - DEBUG=false
      # - SECRET_KEY=your-random-secret-here
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:5000/')"]
      interval: 30s
      timeout: 10s
      retries: 3

volumes:
  emby_dupes_data:
    driver: local
```

The `emby_dupes_data` named volume persists your `config.json` (connection settings, scoring config, export config) and `exclusions.json` (skipped duplicates) between container restarts and upgrades.

## Environment Variables

| Variable     | Default               | Description                              |
|--------------|-----------------------|------------------------------------------|
| `PORT`       | `5000`                | Port the app listens on                  |
| `DEBUG`      | `false`               | Enable Flask debug mode                  |
| `DATA_DIR`   | `/app/data`           | Directory for `config.json` and `exclusions.json` |
| `SECRET_KEY` | *(built-in default)*  | Flask session secret key (change in production) |

## Configuration

### Emby Server Connection

1. Open the app in your browser
2. Enter your Emby server **host** (IP or domain), **port** (default 8096), and **API key**
3. Click **Test Connection** to verify, then **Save**

#### Getting Your Emby API Key

1. Log into your Emby server dashboard as an admin
2. Go to **Advanced -> API Keys**
3. Click **+ New API Key**, give it a name (e.g. `dupe-finder`)
4. Copy the generated key and paste it into the app

### Scoring Configuration

Click the **Scoring Settings** button in the toolbar to open the scoring configuration modal. All scores are summed to produce a total quality score per file.

#### Score Components

| Component | How It's Calculated |
|-----------|-------------------|
| **Audio Codec** | Lookup score by codec name (e.g. TrueHD = 4500, FLAC = 2500, AAC = 1000) |
| **Video Codec** | Lookup score by codec name (e.g. H.264 = 10000, HEVC = 5000, VC1 = 3000) |
| **Video Resolution** | Lookup score by resolution class: 4K = 20000, 1080p = 10000, 720p = 5000, 480p = 3000, SD = 1000 |
| **Filename Patterns** | Glob patterns matched against filename (e.g. `*Remux*` = +20000, `*HDTV*` = -1000) |
| **Video Bitrate** | `bitrate_kbps * 2` |
| **Video Dimensions** | `width * 2 + height * 2` |
| **Audio Channels** | `channels * 1000` |
| **File Size** | `size_bytes / 100000` (optional, enabled by default) |

You can add, remove, or modify any codec/resolution score and add filename glob patterns with positive (bonus) or negative (penalty) values.

#### Auto-Select Losers

When enabled, scanning will automatically select all duplicate files except the highest-scored one in each group, making it easy to quickly export or delete lower-quality copies.

### Export Path Configuration

Configure how file paths are transformed when exporting:

- **Prefix** - prepend text to each path
- **Suffix** - append text to each path
- **Strip Filename** - export directory paths only
- **Path Mappings** - find/replace rules to transform paths (e.g. map Emby's `/media/movies/` to your local `/mnt/movies/`)

## How Duplicate Detection Works

1. The app fetches all movies from your Emby library via the `/Items` API endpoint
2. Movies are grouped by **IMDb ID**, **TMDb ID**, or **normalized title + year** (in that priority order)
3. Any group with 2+ files (across separate items or multiple media sources within one item) is flagged as a duplicate
4. Each file is scored using the configured scoring algorithm
5. Files within each group are sorted by score (highest first) - the top file is marked as "BEST"
6. Exclusions are applied: excluded files (matched by path) are ignored, and excluded groups are hidden as long as they contain only the files they had when excluded - if a new copy appears, the group is reported again

## Tech Stack

- **Backend**: Python 3.12, Flask, Gunicorn
- **Frontend**: Vanilla JavaScript (single-page application, no build step)
- **Container**: Docker with non-root user

## Notes

- The app **reads** from your Emby server by default - deletion requires explicit action via the Delete button
- Exported `.txt` files contain one file path per line
- Settings are stored in `data/config.json`; exclusions are stored separately in `data/exclusions.json`
- The app was tested with Emby 4.9.3.0
