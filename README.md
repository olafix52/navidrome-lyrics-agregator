# 🎵 Navidrome Lyrics Aggregator

[![CI](https://github.com/olafix52/navidrome-lyrics-agregator/actions/workflows/ci.yml/badge.svg)](https://github.com/olafix52/navidrome-lyrics-agregator/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Docker Support](https://img.shields.io/badge/docker-ready-blue?logo=docker)](https://www.docker.com/)

> **An automated, high-speed asynchronous lyrics aggregator and daemon for the [Navidrome](https://www.navidrome.org/) music server. Fetches synchronized lyrics in the highest possible quality (syllable-by-syllable TTML, word-synced Lyricsfile YAML, line-synced LRC), embeds them directly into audio tags, and notifies Navidrome in real time.**

*Read this in other languages: [English](README.md) | [Polski](README.pl.md)*

---

## 🌟 Key Features

- **Quality Cascade – 13 Lyrics Providers:**
  1. `amll` – Apple Music-Like Lyrics DB (TTML syllable-level sync and multi-singer support).
  2. `apple_music` – Apple Music Catalog / AMLL lookup bridge (TTML).
  3. `rmmrevival` – RMM Revival / Apple Music Worker (word-synced TTML & LRC).
  4. `unison` – Crowdsourced Better Lyrics Unison API (TTML, YAML, LRC).
  5. `binilyrics` – BiniLyrics / Aligned REST API (TTML & LRC).
  6. `lrclib` – LRCLIB Database (word-synced YAML & line-synced LRC).
  7. `musixmatch` – Musixmatch Desktop API (RichSync word-synced TTML & LRC).
  8. `qqmusic` – QQ Music / Tencent API (QRC word-synced TTML & LRC).
  9. `kuwo` – Kuwo Music API (synced LRC).
  10. `netease` – NetEase Cloud Music 163 API (YRC word-synced TTML & LRC).
  11. `kugou` – Kugou Music API (KRC word-synced TTML & LRC).
  12. `lyricsify` – Lyricsify community database (with FlareSolverr support).
  13. `genius` – Genius API + HTML scraper (optional unsynchronized fallback).

- **Flexible Storage & Audio Tag Writing (Embedded Lyrics):**
  - **Sidecar files:** Atomic saving of companion files (`.ttml`, `.lyricsfile.yaml`, `.lrc`, `.txt`) with quality resolution order (`.ttml` > `.yaml` > `.lrc`).
  - **Enhanced LRC & Word/Syllable Karaoke Timing:**
    - When word-level timing is found (TTML, Lyricsfile YAML), the aggregator embeds **Enhanced LRC (ELRC)** with `<mm:ss.xx>` word timestamps into the `LYRICS` / `USLT` tags.
    - Enables smooth word-by-word karaoke animations in **Feishin** (via OpenSubsonic Song Lyrics v2) and **Symfonium**.
    - Stores raw Apple Music XML in the `LYRICS_TTML` Vorbis tag for advanced clients.
    - Emits word-level millisecond timestamps in ID3 `SYLT` frames for MP3.
  - **Embedded Audio Tags:** Safe, non-destructive embedding into audio metadata via `mutagen`:
    - **MP3 (ID3v2.4):** `USLT` (Enhanced LRC/plain text), `SYLT` (millisecond-accurate karaoke timing), and `TXXX:LYRICS`.
    - **FLAC, OGG, Opus:** Vorbis comments `LYRICS` (Enhanced LRC), `UNSYNCEDLYRICS`, and `LYRICS_TTML`.
    - **M4A / MP4 / ALAC:** QuickTime/Apple atom `©lyr` (`\xa9lyr`).
  - **Storage mode selection (`--storage-mode`):** `sidecar` (default), `embedded` (tags only), or `both`.
  - **Custom destination directory (`--output-dir`):** Store sidecars in a separate, isolated folder outside the music directory.

- **Navidrome / Subsonic API Integration:**
  - **Scan Trigger (`--auto-scan`):** Automatically notifies Navidrome via `/rest/startScan.view` as soon as new lyrics are saved, making them available in clients (Feishin, Symfonium) immediately without waiting for scheduled server scans.
  - **Subsonic Remote Discovery (`--subsonic`):** Fetches song metadata directly from Navidrome over the network, allowing the aggregator to run on a separate server or container without mounting `/music`.
  - **Manual control commands:** `trigger-scan` (initiate scan) and `ping-navidrome` (test credentials & connection).

- **Intelligent Matching Engine:**
  - Audio tag extraction powered by `mutagen` (`.flac`, `.mp3`, `.m4a`, `.opus`, `.ogg`, `.wav`, `.aiff`, etc.).
  - Advanced title normalization (strips `(Remastered)`, `[Official Audio]`, `feat.`, `(Live)`, etc.).
  - Song duration deviation guard (default tolerance: $\pm 2.5$ seconds).
  - Fuzzy string similarity validation ($\ge 0.75$ threshold).

- **Operating Modes & Library Management Tools:**
  - `scan` – High-speed library scan with Rich progress bar, concurrency control, and tabular summary.
  - `daemon` – Continuous background service with scheduled scans (e.g. every hour).
  - `watch` – Real-time filesystem events monitor powered by `watchdog` with debouncing.
  - `test-track` – Rapid CLI provider query testing for a single artist and title without writing files.
  - `audit` (or `stats`) – Offline library audit reporting coverage (word-sync, line-sync, unsynced, missing) with JSON/CSV export.
  - `upgrade` – Targeted scan querying providers only for tracks lacking word-sync lyrics, automatically skipping `.ttml`.
  - `prune` – Safe housekeeping tool detecting and removing orphaned lyrics or obsolete lower-quality duplicates.
  - `web` (or `dashboard`) – Minimalist Web UI dashboard with live karaoke music player (ToxiPlays TTML renderer) and manual search.
  - `trigger-scan` – Trigger a library rescan on Navidrome server on demand.
  - `ping-navidrome` – Test connectivity and Subsonic authentication with Navidrome.

---

## 🚀 Quick Start with Docker Compose

The recommended deployment is running the aggregator alongside Navidrome in your `docker-compose.yml`:

```yaml
services:
  navidrome:
    image: deluan/navidrome:latest
    container_name: navidrome
    ports:
      - "4533:4533"
    environment:
      ND_SCANSCHEDULE: 1h
    volumes:
      - ./data/navidrome:/data
      - ./music:/music:ro

  lyrics-aggregator:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: navidrome-lyrics-aggregator
    restart: unless-stopped
    environment:
      - MUSIC_DIR=/music
      - NLA_SCAN_INTERVAL=1h
      - NLA_LOG_LEVEL=INFO
      - NLA_CONCURRENCY=16
      - NLA_STORAGE_MODE=both
      - NLA_NAVIDROME_URL=http://navidrome:4533
      - NLA_NAVIDROME_USER=admin
      - NLA_NAVIDROME_PASSWORD=your_password
      - NLA_NAVIDROME_AUTO_SCAN=true
    volumes:
      - ./music:/music:rw
      - ./config.yaml:/config/config.yaml:ro
    command: ["daemon", "--with-watch"]
    depends_on:
      - navidrome
```

Launch the stack:
```bash
docker compose up -d --build
```

---

## 🛠️ Local Installation (Standalone)

### Requirements:
- Python 3.11 or higher
- `pip` package manager

```bash
# 1. Clone repository and install dependencies
git clone https://github.com/olafix52/navidrome-lyrics-agregator.git
cd navidrome-lyrics-agregator
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Local configuration (optional)
cp config.example.yaml config.local.yaml

# 3. One-off library scan (default: sidecar files)
python -m src.main scan -d /path/to/music

# 4. Fast scan with embedded audio tags & auto Navidrome scan trigger
python -m src.main scan -d /path/to/music --storage-mode both --auto-scan --concurrency 16

# 5. Remote scan via Navidrome Subsonic API (no local music mount required)
python -m src.main scan --subsonic --output-dir /path/to/lyrics --navidrome-url http://localhost:4533 -u admin -p secret

# 6. Test lyrics lookup for a single track
python -m src.main test-track -a "Queen" -t "Bohemian Rhapsody"

# 7. Run continuous daemon with real-time filesystem watcher
python -m src.main daemon -d /path/to/music -i 1h --with-watch

# 8. Offline library audit & export missing lyrics
python -m src.main audit -d /path/to/music --show-missing
python -m src.main audit -d /path/to/music --export-missing missing.csv

# 9. Upgrade lower-quality lyrics to TTML
python -m src.main upgrade -d /path/to/music

# 10. Prune orphaned sidecars and obsolete duplicates (dry-run by default)
python -m src.main prune -d /path/to/music
python -m src.main prune -d /path/to/music --force

# 11. Manually trigger Navidrome library scan or check connection
python -m src.main ping-navidrome
python -m src.main trigger-scan

# 12. Launch lightweight Web UI & live karaoke player
python -m src.main web -p 8080 -d /path/to/music
```

---

## ⚡ Speed & Performance Tuning

To scan large music collections at maximum speed:
- **Increase concurrency (`--concurrency`):** Set `--concurrency 16` or `32` to process tracks in parallel.
- **Skip tracks with existing lyrics (`NLA_UPGRADE_QUALITY=false`):** By default, the aggregator searches for word-sync TTML even if an `.lrc` exists. Disable `upgrade_quality` to only fetch lyrics for completely missing songs:
  ```bash
  NLA_UPGRADE_QUALITY=false python -m src.main scan -d /path/to/music --concurrency 24
  ```
- **Prioritize fast providers:** Restrict the provider cascade to the fastest sources:
  ```bash
  NLA_ENABLED_PROVIDERS="amll,lrclib,netease,kugou" python -m src.main scan -d /path/to/music --concurrency 20
  ```

---

## ⚙️ Configuration (`config.yaml` / `config.local.yaml`)

Configuration can be fine-tuned via `config.yaml` (or `config.example.yaml`):

```yaml
# General settings
music_dir: "/music"
scan_interval: "1h"
watch_debounce_seconds: 3.0
duration_tolerance_seconds: 2.5
min_similarity_score: 0.75

# Storage settings
storage_mode: "both"     # "sidecar", "embedded", or "both"
embed_word_sync: true    # Embed Enhanced LRC (<mm:ss.xx>) and SYLT word-level timestamps for Feishin/Symfonium
output_dir: null         # Optional custom destination folder for sidecars
overwrite: false
upgrade_quality: true
dry_run: false
allow_plain_lyrics: false
concurrency: 16

# Navidrome server connection
navidrome:
  url: "http://localhost:4533"
  user: "admin"
  password: "your_password"
  auto_scan: true        # Trigger scan automatically when new lyrics are saved
  full_scan: false

# Active provider cascade (priority order)
enabled_providers:
  - "amll"
  - "apple_music"
  - "rmmrevival"
  - "unison"
  - "binilyrics"
  - "lrclib"
  - "musixmatch"
  - "qqmusic"
  - "kuwo"
  - "netease"
  - "kugou"
  - "lyricsify"
  - "genius"
```

All parameters can also be configured using environment variables with the `NLA_` prefix (see [.env.example](.env.example)):
- `MUSIC_DIR` or `NLA_MUSIC_DIR` – Path to audio collection directory
- `NLA_STORAGE_MODE` – Storage destination: `sidecar`, `embedded`, or `both`
- `NLA_EMBED_WORD_SYNC` – Embed word-level timing (Enhanced LRC) into audio tags (`true`/`false`)
- `NLA_OUTPUT_DIR` – Custom directory for sidecar files
- `NLA_SCAN_INTERVAL` – Daemon scan interval (e.g. `1h`, `30m`, `3600s`)
- `NLA_CONCURRENCY` – Max concurrent download tasks (default: `4`)
- `NLA_OVERWRITE` – Overwrite existing lyrics (`true`/`false`)
- `NLA_UPGRADE_QUALITY` – Upgrade lower quality lyrics to TTML (`true`/`false`)
- `NLA_NAVIDROME_URL` – Navidrome server URL
- `NLA_NAVIDROME_USER` – Navidrome username
- `NLA_NAVIDROME_PASSWORD` – Navidrome password
- `NLA_NAVIDROME_AUTO_SCAN` – Automatically trigger Navidrome scan (`true`/`false`)
- `NLA_LOG_LEVEL` – Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`)

> [!TIP]
> If you have custom credentials or local paths, place them in `config.local.yaml`. This file is automatically ignored by Git and will never be committed.

---

## 🧪 Unit Tests

Run the complete test suite covering TTML/YAML/LRC parsers, audio tag reading/writing, Subsonic API client, and provider cascades:

```bash
pytest
```
*99 unit tests passing (100% test coverage for all core components).*

---

## 🤝 Contributing

Contributions, issues, and feature requests are welcome! Feel free to check the [CONTRIBUTING.md](CONTRIBUTING.md) guide before opening a PR.

---

## 📄 License

This project is licensed under the terms of the [MIT License](LICENSE).
