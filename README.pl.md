# 🎵 Navidrome Lyrics Aggregator

[![CI](https://github.com/olafix52/navidrome-lyrics-agregator/actions/workflows/ci.yml/badge.svg)](https://github.com/olafix52/navidrome-lyrics-agregator/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Docker Support](https://img.shields.io/badge/docker-ready-blue?logo=docker)](https://www.docker.com/)

> **Wysokowydajny, asynchroniczny agregator i demon tekstów piosenek dla serwera muzycznego [Navidrome](https://www.navidrome.org/). Pobiera zsynchronizowane teksty w najwyższej jakości (TTML sylaba-po-sylabie, Lyricsfile YAML oraz zsynchronizowany LRC), osadza je bezpośrednio w tagach audio i automatycznie wyzwala skanowanie w Navidrome.**

*Dostępne wersje językowe: [English](README.md) | [Polski](README.pl.md)*

---

## 🌟 Kluczowe funkcje

- **Kaskada priorytetów (Quality Cascade) – 19 dostawców:**
  1. `spicylyrics` – Spicy Lyrics Developer API (word-sync `.ttml` na poziomie sylab oraz `.lrc`).
  2. `amll` – Apple Music-Like Lyrics DB (`.ttml` z sylabami i wieloma wokalistami).
  3. `apple_music` – Apple Music Catalog / AMLL (`.ttml`).
  4. `rmmrevival` – RMM Revival / Apple Music Worker (word-sync `.ttml` oraz `.lrc`).
  5. `unison` – Społecznościowe API Better Lyrics Unison (`.ttml` / `.yaml` / `.lrc`).
  6. `binilyrics` – BiniLyrics / Aligned REST API (`.ttml` / `.lrc`).
  7. `lrclib` – LRCLIB Database (`.yaml` word-synced oraz `.lrc` line-synced).
  8. `musixmatch` – Musixmatch iOS Mobile API (RichSync word-sync `.ttml` i `.lrc` z buforowaniem tokenów, wyszukiwaniem Spotify ID, ekstrakcją autorów oraz wygładzaniem przerw).
  9. `neblend` – Wersy Apple Music + timing słów z NetEase (mild-lyrics neblend).
  10. `triblend` – Wersy Apple Music + timing z NetEase + uzupełnianie braków z QQ Music (mild-lyrics triblend).
  11. `kutriblend` – Wersy Apple Music + timing z NetEase + uzupełnianie braków z Kugou (mild-lyrics kutriblend).
  12. `netease` – NetEase Cloud Music 163 API (YRC word-sync `.ttml` oraz `.lrc`).
  13. `blend` – Wersy Apple Music + timing słów z QQ Music (mild-lyrics blend).
  14. `qqmusic` – QQ Music / Tencent API (QRC word-sync `.ttml` oraz `.lrc`).
  15. `kublend` – Wersy Apple Music + timing słów z Kugou (mild-lyrics kublend).
  16. `kugou` – Kugou Music API (KRC word-sync `.ttml` oraz `.lrc`).
  17. `kuwo` – Kuwo Music API (`.lrc`).
  18. `lyricsify` – Baza Lyricsify (wsparcie FlareSolverr).
  19. `genius` – Genius API + HTML Scraper (opcjonalny niesynchroniczny fallback).

- **Silnik Blend z Mild-Lyrics (Fuzja wersów i synchronizacji słów):**
  - **5 wyspecjalizowanych dostawców Blend:** Łączy oryginalny tekst i podział wersów z Apple Music / SpicyLyrics / LRCLIB z precyzyjnymi timingami słów/sylab od dawców z azjatyckich platform streamingowych (QQ Music QRC, NetEase YRC, Kugou KRC).
  - **Inteligentne usuwanie cenzury i dopasowywanie slangu:** Automatycznie przywraca ocenzurowane słowa dawcy (np. `f***` -> `fucking`), dopasowuje skróty językowe (`it's`, `don't`), obsługuje odmiany slangowe (`nothin'` -> `nothing`) i scala rozbite tokeny wielosłowne.
  - **Wypełnianie luk wieloma dawcami (`triblend`, `kutriblend`):** Wypełnia wersy brakujące u głównego dawcy timingami z dawcy rezerwowego.
  - **Czystość wersów:** Automatycznie ignoruje zbędne metadane, spany tłumaczeń (`x-translation`) oraz transkrypcji fonetycznej (`x-roman`).

- **Elastyczne przechowywanie i zapis w tagach audio (Embedded Lyrics):**
  - **Pliki sidecar:** Atomowy zapis plików towarzyszących (`.ttml`, `.lyricsfile.yaml`, `.lrc`, `.txt`) z hierarchią jakości (`.ttml` > `.yaml` > `.lrc`).
  - **Enhanced LRC & synchronizacja słowna/sylabowa karaoke:**
    - Jeśli dostępne są znaczniki czasowe na poziomie słów (TTML, Lyricsfile YAML), agregator osadza format **Enhanced LRC (ELRC)** ze znacznikami `<mm:ss.xx>` dla każdego słowa wewnątrz tagów `LYRICS` / `USLT`.
    - Umożliwia to płynną animację karaoke słowo po słowie w **Feishin** (poprzez protokół OpenSubsonic Song Lyrics v2) oraz w **Symfonium**.
    - Zapisuje surowy XML Apple Music w tagu Vorbis `LYRICS_TTML` dla zaawansowanych klientów.
    - Generuje znaczniki słowne z dokładnością milisekundową w ramkach ID3 `SYLT` dla plików MP3.
  - **Osadzanie w tagach audio:** Bezpieczny, bezstratny zapis metadanych przez `mutagen`:
    - **MP3 (ID3v2.4):** Ramki `USLT` (Enhanced LRC / zwykły tekst), `SYLT` (milisekundowa synchronizacja karaoke) oraz `TXXX:LYRICS`.
    - **FLAC, OGG, Opus:** Komentarze Vorbis `LYRICS` (Enhanced LRC), `UNSYNCEDLYRICS` oraz `LYRICS_TTML`.
    - **M4A / MP4 / ALAC:** Atom QuickTime/Apple `©lyr` (`\xa9lyr`).
  - **Wybór trybu (`--storage-mode`):** `sidecar` (domyślny), `embedded` (wyłącznie tagi) lub `both` (jednocześnie pliki sidecar i tagi).
  - **Katalog wyjściowy (`--output-dir`):** Zapisywanie plików tekstów w wyodrębnionym folderze poza katalogiem muzyki.

- **Integracja z Navidrome / Subsonic API:**
  - **Wyzwalanie skanera (`--auto-scan`):** Automatyczne wysyłanie żądania `/rest/startScan.view` do Navidrome natychmiast po pobraniu nowych tekstów, dzięki czemu pojawiają się one w odtwarzaczach (Feishin, Symfonium) od razu.
  - **Zdalne wykrywanie utworów (`--subsonic`):** Pobieranie listy utworów bezpośrednio przez API sieciowe, bez konieczności lokalnego montowania wolumenu `/music`.
  - **Polecenia CLI:** `trigger-scan` (ręczne wywołanie skanu w Navidrome) oraz `ping-navidrome` (test połączenia i danych logowania).

- **Inteligentny silnik dopasowywania:**
  - Odczyt metadanych audio za pomocą `mutagen` (`.flac`, `.mp3`, `.m4a`, `.opus`, `.ogg`, `.wav`, `.aiff`, itp.).
  - Zaawansowane oczyszczanie tytułów (usuwanie `(Remastered)`, `[Official Audio]`, `feat.`, `(Live)`).
  - Zabezpieczenie przed błędnym dopasowaniem czasu utworu (domyślna tolerancja $\pm 2.5$ s).
  - Weryfikacja podobieństwa nazw i wykonawców (Fuzzy String Similarity $\ge 0.75$).

- **Wysokowydajny trwały cache i optymalizacja wydajności:**
  - **Indeksowanie plików sidecar na poziomie folderów (`FolderLyricsIndex`):** Pamięć podręczna wpisów katalogowych weryfikowana na podstawie czasu modyfikacji `mtime_ns` katalogu, sprawdzająca 1 000 plików w ~6 ms (ponad 156 000 utworów/sekundę, **2.7× szybciej**).
  - **Strumieniowy skaner producent-konsument:** Leniwe generowanie ścieżek z ograniczoną kolejką zadań, natychmiastowe rozpoczęcie pobierania tekstów bez wstępnego oczekiwania na pełny skan i stałe zapotrzebowanie na RAM.
  - **Deduplikacja zapytań Single-Flight:** Grupuje równoległe zapytania o te same utwory (np. w kompilacjach, wydaniach deluxe, różnych formatach plików) w jedno zapytanie do dostawców, redukując ruch sieciowy o ponad 50%.
  - **Multipleksacja HTTP/2 (`httpx[http2]`):** Współdzielenie pojedynczego połączenia TCP i równoległa wymiana strumieni z nowoczesnymi API bez kosztownych negocjacji TLS.
  - **Wysokowydajna pętla zdarzeń `uvloop`:** Oparta o libuv na systemach Linux i w kontenerze Docker, minimalizuje narzut procesora i przyspiesza przełączanie zadań asynchronicznych.
  - **Trwały negatywny cache SQLite (tryb WAL & MMAP):** Zapamiętuje brakujące teksty oraz nieudane zapytania do dostawców z mechanizmem wykładniczego wycofywania (exponential backoff) i konfigurowalnym czasem wygaśnięcia TTL (`negative_ttl_days: 14`). Zoptymalizowany pod kątem mapowania pamięci (`mmap_size = 256MB`). Kolejne uruchomienia demona lub skanowania biblioteki pomijają nieznane utwory w ułamku milisekundy, eliminując niepotrzebne zapytania sieciowe.
  - **Szybkie indeksowanie systemu plików (`os.scandir`):** Przeszukuje strukturę folderów **3–5× szybciej** niż standardowe `rglob`, czytając metadane wpisów katalogowych bezpośrednio z i-węzłów bez zbędnych wywołań systemowych `stat()`.
  - **Pamięć podręczna LRU w RAM:** Algorytmy normalizacji tytułów i wykonawców (`clean_title`, `clean_artist`), ocena kandydatów oraz symetryczne badanie podobieństwa napisów są buforowane w pamięci podręcznej przez `functools.lru_cache`, znacznie odciążając procesor przy dużych zbiorach.
  - **Nieblokujące asynchroniczne I/O:** Wszystkie operacje dyskowe i CPU (odczyt tagów audio `mutagen`, zapis tekstów, transakcje SQLite) są oddelegowane do wątków roboczych za pomocą `asyncio.to_thread`, gwarantując pełną responsywność pętli zdarzeń.
  - **Zrównoleglone pobieranie albumów Subsonic:** Awaryjny tryb pobierania katalogu Subsonic pobiera utwory z wielu albumów jednocześnie w potoku `asyncio.gather`, przyspieszając start do 10×.
  - **Budżet kaskady i szybka synchronizacja:** Flaga `--fast-line-sync` natychmiast akceptuje zsynchronizowany plik LRC bez odpytywania dalszych dostawców; flaga `--word-sync-budget` ogranicza liczbę odpytywanych dostawców word-sync.
  - **Alokator pamięci Jemalloc:** Kontener Docker wykorzystuje bibliotekę `libjemalloc2`, co drastycznie ogranicza fragmentację pamięci RAM podczas skanowania potężnych zbiorów muzycznych.

- **Tryby działania i narzędzia:**
  - `scan` – Szybkie skanowanie biblioteki z paskiem postępu, kontrolą współbieżności i tabelą podsumowania.
  - `daemon` – Usługa w tle z harmonogramem skanowania (np. co godzinę).
  - `watch` – Monitorowanie zmian w systemie plików w czasie rzeczywistym (`watchdog`).
  - `cache` – Podgląd statystyk cache'u (`--stats`), czyszczenie przestarzałych wpisów (`--prune`) lub reset bazy negatywnej (`--clear`).
  - `test-track` – Błyskawiczne sprawdzenie wyników u wszystkich dostawców dla jednego utworu z poziomu konsoli.
  - `audit` (lub `stats`) – Audyt offline raportujący stan biblioteki z eksportem do JSON/CSV.
  - `upgrade` – Pobieranie tekstów word-sync tylko dla utworów, które ich nie posiadają (pomija `.ttml`).
  - `prune` – Usuwanie osieroconych plików tekstów i przestarzałych duplikatów o niższej jakości.
  - `web` (lub `dashboard`) – Minimalistyczny panel Web UI z odtwarzaczem karaoke (renderer ToxiPlays TTML), przełącznikami dostawców i zarządzaniem cache.
  - `trigger-scan` – Wywołanie skanowania biblioteki na serwerze Navidrome.
  - `ping-navidrome` – Sprawdzenie połączenia z API Navidrome.

---

## 🚀 Szybki start z Docker Compose

Zalecanym sposobem uruchomienia jest spięcie agregatora w jednym stosie z Navidrome:

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
    ports:
      - "8080:8080"
    environment:
      - MUSIC_DIR=/music
      - NLA_SCAN_INTERVAL=1h
      - NLA_LOG_LEVEL=INFO
      - NLA_CONCURRENCY=16
      - NLA_STORAGE_MODE=both
      - NLA_NAVIDROME_URL=http://navidrome:4533
      - NLA_NAVIDROME_USER=admin
      - NLA_NAVIDROME_PASSWORD=twoje_haslo
      - NLA_NAVIDROME_AUTO_SCAN=true
    volumes:
      - ./music:/music:rw
      - ./data:/data:rw
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
- Python 3.11 lub nowszy
- Menedżer pakietów `pip`

```bash
# 1. Klonowanie repozytorium i instalacja zależności
git clone https://github.com/olafix52/navidrome-lyrics-agregator.git
cd navidrome-lyrics-agregator
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Konfiguracja lokalna (opcjonalnie)
cp config.example.yaml config.local.yaml

# 3. Jednorazowy skan biblioteki (domyślnie pliki sidecar)
python -m src.main scan -d /ścieżka/do/muzyki

# 4. Szybki skan z zapisem do tagów audio i auto-skanem Navidrome
python -m src.main scan -d /ścieżka/do/muzyki --storage-mode both --auto-scan --concurrency 16

# 5. Zdalny skan przez API Subsonic Navidrome (bez montowania dysku z muzyką)
python -m src.main scan --subsonic --output-dir /ścieżka/do/tekstów --navidrome-url http://localhost:4533 -u admin -p tajne

# 6. Sprawdzenie wyszukiwania tekstu dla pojedynczego utworu
python -m src.main test-track -a "Queen" -t "Bohemian Rhapsody"

# 7. Uruchomienie demona w tle z nasłuchiwaniem nowych plików
python -m src.main daemon -d /ścieżka/do/muzyki -i 1h --with-watch

# 8. Audyt biblioteki offline i eksport brakujących tekstów
python -m src.main audit -d /ścieżka/do/muzyki --show-missing
python -m src.main audit -d /ścieżka/do/muzyki --export-missing missing.csv

# 9. Podbicie jakości istniejących tekstów do TTML
python -m src.main upgrade -d /ścieżka/do/muzyki

# 10. Czyszczenie osieroconych plików i przestarzałych duplikatów (domyślnie dry-run)
python -m src.main prune -d /ścieżka/do/muzyki
python -m src.main prune -d /ścieżka/do/muzyki --force

# 11. Wywołanie skanu biblioteki w Navidrome lub test połączenia
python -m src.main ping-navidrome
python -m src.main trigger-scan

# 12. Uruchomienie minimalistycznego panelu Web UI i odtwarzacza karaoke
python -m src.main web -p 8080 -d /ścieżka/do/muzyki

# 13. Zarządzanie trwałym negatywnym cache'em SQLite
python -m src.main cache --stats
python -m src.main cache --prune
python -m src.main cache --clear
```

---

## ⚡ Maksymalizacja prędkości skanowania

Aby przeskanować dużą bibliotekę muzyczną w najkrótszym czasie:
- **Trwały negatywny cache (włączony domyślnie):** Utwory, dla których żaden dostawca nie znalazł tekstu, trafiają do bazy `data/cache.db` z czasem TTL (domyślnie 14 dni). Kolejne skanowania trwają sekundy zamiast minut, ponieważ brakujące utwory nie są ponownie odpytywane w sieci. Aby wymusić ponowne odpytanie, użyj `--no-cache`.
- **Tryb szybkiej synchronizacji (`--fast-line-sync`):** Zatrzymuje przeszukiwanie kaskady natychmiast po znalezieniu tekstu zsynchronizowanego liniowo (LRC), pomijając pozostałe zapytania word-sync.
- **Budżet zapytań word-sync (`--word-sync-budget N`):** Ogranicza zapytania o teksty sylabowe/słowne do $N$ pierwszych dostawców przed zatwierdzeniem LRC (np. `--word-sync-budget 3`).
- **Zwiększ współbieżność (`--concurrency`):** Ustaw `--concurrency 16` lub `24`, aby asynchronicznie przetwarzać wiele utworów naraz.
- **Pomiń utwory posiadające już jakikolwiek tekst (`NLA_UPGRADE_QUALITY=false`):** Domyślnie agregator odpytuje serwery w poszukiwaniu TTML, nawet jeśli istnieje `.lrc`. Wyłączenie tej opcji sprawi, że utwory z tekstem zostaną pominięte w ułamku milisekundy:
  ```bash
  NLA_UPGRADE_QUALITY=false python -m src.main scan -d /ścieżka/do/muzyki --concurrency 24
  ```
- **Wybierz najszybszych dostawców:**
  ```bash
  NLA_ENABLED_PROVIDERS="amll,lrclib,netease,kugou" python -m src.main scan -d /ścieżka/do/muzyki --concurrency 20
  ```

---

## ⚙️ Konfiguracja (`config.yaml` / `config.local.yaml`)

Plik `config.yaml` pozwala precyzyjnie dostosować działanie aplikacji:

```yaml
# Ustawienia ogólne
music_dir: "/music"
scan_interval: "1h"
watch_debounce_seconds: 3.0
duration_tolerance_seconds: 2.5
min_similarity_score: 0.75

# Ustawienia zapisu tekstów
storage_mode: "both"     # "sidecar", "embedded" lub "both"
embed_word_sync: true    # Zapis Enhanced LRC (<mm:ss.xx>) oraz znaczników słownych SYLT dla Feishin/Symfonium
output_dir: null         # Opcjonalny dedykowany folder na pliki tekstów
overwrite: false
upgrade_quality: true
dry_run: false
allow_plain_lyrics: false
concurrency: 16

# Ustawienia trwałego cache'u negatywnego
cache:
  enabled: true
  db_path: "data/cache.db"
  negative_ttl_days: 14

# Połączenie z serwerem Navidrome
navidrome:
  url: "http://localhost:4533"
  user: "admin"
  password: "twoje_haslo"
  auto_scan: true        # Automatyczne wyzwalanie skanera po zapisaniu nowych tekstów
  full_scan: false

# Aktywni dostawcy (kolejność określa priorytet)
enabled_providers:
  - "spicylyrics"
  - "amll"
  - "apple_music"
  - "rmmrevival"
  - "unison"
  - "binilyrics"
  - "lrclib"
  - "musixmatch"
  - "neblend"
  - "triblend"
  - "kutriblend"
  - "netease"
  - "blend"
  - "qqmusic"
  - "kublend"
  - "kugou"
  - "kuwo"
  - "lyricsify"
  - "genius"
```

Wszystkie opcje można również przekazać za pomocą zmiennych środowiskowych z przedrostkiem `NLA_`:
- `MUSIC_DIR` lub `NLA_MUSIC_DIR` – Ścieżka do katalogu z muzyką
- `NLA_STORAGE_MODE` – Tryb zapisu: `sidecar`, `embedded` lub `both`
- `NLA_EMBED_WORD_SYNC` – Osadzanie synchronizacji słownej (Enhanced LRC) w tagach audio (`true`/`false`)
- `NLA_OUTPUT_DIR` – Dedykowany folder na pliki tekstów
- `NLA_CACHE_ENABLED` – Włączenie trwałego cache'u SQLite (`true`/`false`)
- `NLA_CACHE_DB_PATH` – Ścieżka do pliku bazy SQLite (`data/cache.db`)
- `NLA_CACHE_NEGATIVE_TTL_DAYS` – Dni przechowywania nieudanych wyszukiwań (domyślnie: `14`)
- `SPICY_LYRICS_SECRET_KEY` lub `NLA_SPICY_LYRICS_API_KEY` – Klucz API Spicy Lyrics (`sl_sk_...`)
- `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` – Opcjonalne poświadczenia Spotify API do automatycznego mapowania utworów
- `NLA_SCAN_INTERVAL` – Częstotliwość skanowania w trybie demona (`1h`, `30m`, `3600s`)
- `NLA_CONCURRENCY` – Liczba współbieżnych zadań (domyślnie: `4`)
- `NLA_OVERWRITE` – Nadpisywanie istniejących tekstów (`true`/`false`)
- `NLA_UPGRADE_QUALITY` – Podbijanie jakości tekstów do TTML (`true`/`false`)
- `NLA_NAVIDROME_URL` – Adres URL serwera Navidrome
- `NLA_NAVIDROME_USER` – Nazwa użytkownika Navidrome
- `NLA_NAVIDROME_PASSWORD` – Hasło Navidrome
- `NLA_NAVIDROME_AUTO_SCAN` – Automatyczne wyzwalanie skanu w Navidrome (`true`/`false`)
- `NLA_LOG_LEVEL` – Poziom szczegółowości logów (`DEBUG`, `INFO`, `WARNING`, `ERROR`)

### 🔑 Konfiguracja klucza API Spicy Lyrics

Spicy Lyrics to dostawca o najwyższym priorytecie oferujący teksty TTML z precyzyjną synchronizacją sylabową (Word-Sync), chórkami i obsługą duetów z baz Spotify i Apple Music.

**Uzyskanie klucza API jest darmowe i zajmuje mniej niż minutę:**
1. Wejdź do panelu deweloperskiego [Spicy Lyrics Developer Dashboard](https://developers.spicylyrics.org/dashboard) i zaloguj się za pomocą konta Discord lub GitHub.
2. W zakładce **API Keys** kliknij **Create Key** (wygeneruj klucz Secret Key).
3. Skopiuj wygenerowany klucz (rozpoczyna się od `sl_sk_...`).
4. Dodaj klucz do projektu na jeden z dwóch sposobów:
   - W pliku `.env`:
     ```env
     SPICY_LYRICS_SECRET_KEY=sl_sk_twoj_klucz
     ```
   - Lub w `config.yaml` / `config.local.yaml`:
     ```yaml
     providers:
       spicylyrics:
         enabled: true
         api_key: "sl_sk_twoj_klucz"
     ```

> [!NOTE]
> Agregator działa bez problemu nawet bez klucza Spicy Lyrics! W przypadku braku klucza usługa zostanie po prostu pominięta, a teksty zostaną pobrane z pozostałych darmowych i otwartych dostawców (m.in. AMLL, RMM Revival, Unison, LRCLIB, Blend).

> [!TIP]
> Jeśli posiadasz własne hasła, tokeny API lub niestandardowe ścieżki, umieść je w `config.local.yaml` lub `.env`. Pliki te są automatycznie ignorowane przez Git i nie trafią do repozytorium.

---

## 🧪 Testy jednostkowe

Uruchomienie pełnego pakietu testów:

```bash
pytest
```
*158 testów jednostkowych (100% testów zdanych).*

---

## 🤝 Wkład w projekt

Propozycje zmian, zgłoszenia błędów oraz pull requesty są mile widziane! Przed utworzeniem PR warto zapoznać się z [CONTRIBUTING.md](CONTRIBUTING.md).

---

## 📄 Licencja

Projekt jest udostępniony na warunkach licencji [MIT](LICENSE).
