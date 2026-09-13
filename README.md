# 🎵 Navidrome Lyrics Aggregator

[![CI](https://github.com/olafix52/navidrome-lyrics-agregator/actions/workflows/ci.yml/badge.svg)](https://github.com/olafix52/navidrome-lyrics-agregator/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Docker Support](https://img.shields.io/badge/docker-ready-blue?logo=docker)](https://www.docker.com/)

> **An automated, asynchronous sidecar daemon for the [Navidrome](https://www.navidrome.org/) music server that fetches synchronized lyrics in the highest possible quality: syllable-by-syllable TTML, word-synced Lyricsfile YAML, and line-synced LRC.**

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

- **Intelligent Matching Engine:**
  - Audio tag extraction powered by `mutagen` (`.flac`, `.mp3`, `.m4a`, `.opus`, `.ogg`, `.wav`, `.aiff`, etc.).
  - Advanced title normalization (strips `(Remastered)`, `[Official Audio]`, `feat.`, `(Live)`, etc.).
  - Song duration deviation guard (default tolerance: $\pm 2.5$ seconds).
  - Fuzzy string similarity validation ($\ge 0.75$ threshold).

- **Sidecar File Management:**
  - Existing file resolution order: `song.ttml` > `song.yaml` > `song.lrc`.
  - Atomic writing to prevent file corruption during interrupts.
  - Automatic quality upgrading (e.g. upgrades existing `.lrc` to `.ttml` when syllable-level sync becomes available).

- **Operating Modes:**
  - `scan` – One-time library scan with Rich terminal progress bars and tabular summary.
  - `daemon` – Continuous scheduled scans in the background (e.g. every hour).
  - `watch` – Real-time filesystem events monitor powered by `watchdog` with event debouncing.
  - `test-track` – Rapid CLI provider query testing for a single artist and title without disk modifications.

---

## 🚀 Quick Start with Docker Compose

The recommended deployment is pairing the aggregator container alongside Navidrome in your `docker-compose.yml`:

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
      - NLA_CONCURRENCY=4
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

# 3. One-off library scan
python -m src.main scan -d /path/to/music

# 4. Test lyrics lookup for a single track
python -m src.main test-track -a "Queen" -t "Bohemian Rhapsody"

# 5. Run continuous daemon with real-time filesystem watcher
python -m src.main daemon -d /path/to/music -i 1h --with-watch
```

---

## ⚙️ Configuration (`config.yaml` / `config.local.yaml`)

Configuration can be fine-tuned via `config.yaml` (or `config.example.yaml`):

```yaml
music_dir: "/music"
scan_interval: "1h"
watch_debounce_seconds: 3.0
duration_tolerance_seconds: 2.5
min_similarity_score: 0.75
overwrite: false
upgrade_quality: true
dry_run: false
allow_plain_lyrics: false
concurrency: 4

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
- `NLA_SCAN_INTERVAL` – Daemon scan interval (e.g. `1h`, `30m`, `3600s`)
- `NLA_CONCURRENCY` – Max concurrent download tasks (default: `4`)
- `NLA_OVERWRITE` – Overwrite existing lyrics files (`true`/`false`)
- `NLA_UPGRADE_QUALITY` – Upgrade lower quality lyrics to TTML (`true`/`false`)
- `NLA_LOG_LEVEL` – Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`)

> [!TIP]
> If you have custom API tokens (e.g. Apple Music developer tokens or Genius client token), place them in `config.local.yaml`. This file is automatically ignored by Git and will never be committed.

---

## 🧪 Unit Tests

Run the test suite covering TTML, LRC, and YAML parsers, metadata normalization, and provider responses:

```bash
pytest
```

---

## 🤝 Contributing

Contributions, issues, and feature requests are welcome! Feel free to check the [CONTRIBUTING.md](CONTRIBUTING.md) guide before opening a PR.

---

## 📄 License

This project is licensed under the terms of the [MIT License](LICENSE).
