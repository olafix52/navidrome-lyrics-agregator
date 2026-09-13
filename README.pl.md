# 🎵 Navidrome Lyrics Aggregator

[![CI](https://github.com/olafix52/navidrome-lyrics-agregator/actions/workflows/ci.yml/badge.svg)](https://github.com/olafix52/navidrome-lyrics-agregator/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Docker Support](https://img.shields.io/badge/docker-ready-blue?logo=docker)](https://www.docker.com/)

> **Automatyczny, asynchroniczny demon sidecar dla serwera muzycznego [Navidrome](https://www.navidrome.org/) pobierający zsynchronizowane teksty piosenek w najwyższej możliwej jakości (TTML słowo-po-słowie, Lyricsfile YAML oraz zsynchronizowany LRC).**

*Dostępne wersje językowe: [English](README.md) | [Polski](README.pl.md)*

---

## 🌟 Kluczowe funkcje

- **Kaskada priorytetów (Quality Cascade) – 13 dostawców:**
  1. `amll` – Apple Music-Like Lyrics DB (`.ttml` z sylabami i wieloma wokalistami).
  2. `apple_music` – Apple Music Catalog / AMLL (`.ttml`).
  3. `rmmrevival` – RMM Revival / Apple Music Worker (word-sync `.ttml` oraz `.lrc`).
  4. `unison` – Społecznościowe API Better Lyrics Unison (`.ttml` / `.yaml` / `.lrc`).
  5. `binilyrics` – BiniLyrics / Aligned REST API (`.ttml` / `.lrc`).
  6. `lrclib` – LRCLIB Database (`.yaml` word-synced oraz `.lrc` line-synced).
  7. `musixmatch` – Musixmatch Desktop API (RichSync word-sync `.ttml` oraz `.lrc`).
  8. `qqmusic` – QQ Music / Tencent API (QRC word-sync `.ttml` oraz `.lrc`).
  9. `kuwo` – Kuwo Music API (`.lrc`).
  10. `netease` – NetEase Cloud Music 163 API (YRC word-sync `.ttml` oraz `.lrc`).
  11. `kugou` – Kugou Music API (KRC word-sync `.ttml` oraz `.lrc`).
  12. `lyricsify` – Baza Lyricsify (wsparcie FlareSolverr).
  13. `genius` – Genius API + HTML Scraper (opcjonalny niesynchroniczny fallback).

- **Inteligentny Matching Engine:**
  - Odczyt metadanych przez `mutagen` (`.flac`, `.mp3`, `.m4a`, `.opus`, `.ogg`, `.wav`, `.aiff`, etc.).
  - Zaawansowana normalizacja tytułów (usuwanie `(Remastered)`, `[Official Audio]`, `feat.`, `(Live)`).
  - Weryfikacja zgodności czasu trwania (domyślna tolerancja $\pm 2.5$ s).
  - Weryfikacja podobieństwa tekstu i wykonawcy (Fuzzy String Similarity $\ge 0.75$).

- **Zarządzanie plikami Sidecar:**
  - Sprawdzanie istniejących plików obok audio: `utwór.ttml` > `utwór.yaml` > `utwór.lrc`.
  - Atomowy zapis (ochrona przed uszkodzeniem plików przy przerwaniu).
  - Opcja automatycznego podbijania jakości (np. zamiana `.lrc` na `.ttml` jeśli znaleziono wersję sylabową).

- **Tryby działania:**
  - `scan` – jednorazowe przeskanowanie biblioteki z estetycznym paskiem postępu i podsumowaniem tabelarycznym.
  - `daemon` – cykliczne skanowanie w tle (np. co 1 godzinę).
  - `watch` – monitorowanie zmian na systemie plików w czasie rzeczywistym (`watchdog` z debouncingiem).
  - `test-track` – szybkie testowanie odpytywania dostawców dla pojedynczego utworu bezpośrednio z konsoli.

---

## 🚀 Szybki start z Docker Compose

Najwygodniejszym sposobem uruchomienia jest spięcie kontenera w jednym stosie z Navidrome:

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

Uruchomienie:
```bash
docker compose up -d --build
```

---

## 🛠️ Uruchomienie lokalne (bez Dockera)

### Wymagania:
- Python 3.11+
- Zarządca pakietów `pip`

```bash
# 1. Klonowanie i instalacja zależności
git clone https://github.com/olafix52/navidrome-lyrics-agregator.git
cd navidrome-lyrics-agregator
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Konfiguracja lokalna (opcjonalnie)
cp config.example.yaml config.local.yaml

# 3. Jednorazowe przeskanowanie folderu z muzyką
python -m src.main scan -d /sciezka/do/muzyki

# 4. Testowe odpytanie dostawców dla pojedynczego utworu
python -m src.main test-track -a "Queen" -t "Bohemian Rhapsody"

# 5. Uruchomienie demona w tle z nasłuchiwaniem plików
python -m src.main daemon -d /sciezka/do/muzyki -i 1h --with-watch
```

---

## ⚙️ Konfiguracja (`config.yaml` / `config.local.yaml`)

Plik `config.yaml` (lub `config.example.yaml`) pozwala na pełne dostosowanie zachowania:

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

Każdą opcję można również skonfigurować za pomocą zmiennych środowiskowych z prefiksem `NLA_` (zobacz [.env.example](.env.example)):
- `MUSIC_DIR` lub `NLA_MUSIC_DIR` – katalog z muzyką
- `NLA_SCAN_INTERVAL` – interwał skanowania (np. `1h`, `30m`)
- `NLA_CONCURRENCY` – liczba współbieżnych zapytań (np. `4`)
- `NLA_OVERWRITE` – nadpisywanie istniejących tekstów (`true`/`false`)
- `NLA_UPGRADE_QUALITY` – podbijanie jakości do TTML (`true`/`false`)
- `NLA_LOG_LEVEL` – poziom logowania (`DEBUG`, `INFO`, `WARNING`, `ERROR`)

> [!TIP]
> Jeśli posiadasz własne klucze API (np. token deweloperski Apple Music lub token Genius), umieść je w pliku `config.local.yaml`. Plik ten jest automatycznie ignorowany przez Git i nie zostanie przypadkowo opublikowany.

---

## 🧪 Testy jednostkowe

Projekt posiada zestaw testów jednostkowych pokrywających parsowanie formatów TTML, LRC, Lyricsfile YAML, normalizację metadanych oraz logikę wszystkich providerów:

```bash
pytest
```

---

## 🤝 Wkład w rozwój (Contributing)

Chcesz pomóc w rozwoju projektu, dodać nowego dostawcę tekstów lub zgłosić błąd? Zapoznaj się z [CONTRIBUTING.md](CONTRIBUTING.md).

---

## 📄 Licencja

Projekt udostępniany jest na licencji [MIT](LICENSE).
