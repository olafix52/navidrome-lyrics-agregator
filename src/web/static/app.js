// Navidrome Lyrics Aggregator - Frontend Client
import { TTMLRenderer } from "./ttml-renderer.js";

const ttmlRenderer = new TTMLRenderer("lyrics-container");

let state = {
  page: 1,
  limit: 25,
  filter: "all",
  query: "",
  totalTracks: 0,
  currentTrack: null,
  currentLyrics: null,
  chartInstance: null,
  animFrameId: null,
};

const dom = {
  musicDirBadge: document.getElementById("musicDirBadge"),
  refreshStatsBtn: document.getElementById("refreshStatsBtn"),
  statTotalTracks: document.getElementById("statTotalTracks"),
  statCoveragePct: document.getElementById("statCoveragePct"),
  statWordSync: document.getElementById("statWordSync"),
  statWordSyncPct: document.getElementById("statWordSyncPct"),
  statLineSync: document.getElementById("statLineSync"),
  statLineSyncPct: document.getElementById("statLineSyncPct"),
  statMissing: document.getElementById("statMissing"),
  statMissingPct: document.getElementById("statMissingPct"),
  formatPills: document.getElementById("formatPills"),
  coverageChart: document.getElementById("coverageChart"),

  trackSearchInput: document.getElementById("trackSearchInput"),
  filterTabs: document.querySelectorAll(".tab-btn"),
  tracksTableBody: document.getElementById("tracksTableBody"),
  prevPageBtn: document.getElementById("prevPageBtn"),
  nextPageBtn: document.getElementById("nextPageBtn"),
  pageInfo: document.getElementById("pageInfo"),

  nowPlayingTitle: document.getElementById("nowPlayingTitle"),
  nowPlayingMeta: document.getElementById("nowPlayingMeta"),
  openSearchModalBtn: document.getElementById("openSearchModalBtn"),
  toggleRawBtn: document.getElementById("toggleRawBtn"),
  rawLyricsContainer: document.getElementById("rawLyricsContainer"),
  rawLyricsPre: document.getElementById("rawLyricsPre"),

  audioPlayer: document.getElementById("audioPlayer"),
  playPauseBtn: document.getElementById("playPauseBtn"),
  seekSlider: document.getElementById("seekSlider"),
  currentTimeLabel: document.getElementById("currentTimeLabel"),
  durationLabel: document.getElementById("durationLabel"),
  volumeSlider: document.getElementById("volumeSlider"),
  lyricsOuter: document.getElementById("lyricsOuter"),
  lyricsContainer: document.getElementById("lyrics-container"),
  styleAmlBtn: document.getElementById("styleAmlBtn"),
  styleKaraokeBtn: document.getElementById("styleKaraokeBtn"),
  themeChips: document.querySelectorAll(".theme-chip"),

  searchModal: document.getElementById("searchModal"),
  closeModalBtn: document.getElementById("closeModalBtn"),
  modalArtistInput: document.getElementById("modalArtistInput"),
  modalTitleInput: document.getElementById("modalTitleInput"),
  modalExecuteSearchBtn: document.getElementById("modalExecuteSearchBtn"),
  modalLoadingSpinner: document.getElementById("modalLoadingSpinner"),
  candidatesList: document.getElementById("candidatesList"),
};

// INITIALIZATION
document.addEventListener("DOMContentLoaded", () => {
  initEventListeners();
  fetchStats();
  fetchTracks();
  startKaraokeLoop();
});

function initEventListeners() {
  dom.refreshStatsBtn.addEventListener("click", () => {
    fetchStats();
    fetchTracks();
  });

  // Filter tabs
  dom.filterTabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      dom.filterTabs.forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      state.filter = tab.dataset.filter;
      state.page = 1;
      fetchTracks();
    });
  });

  // Search input debounced
  let searchTimer = null;
  dom.trackSearchInput.addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.query = e.target.value.trim();
      state.page = 1;
      fetchTracks();
    }, 300);
  });

  // Pagination
  dom.prevPageBtn.addEventListener("click", () => {
    if (state.page > 1) {
      state.page--;
      fetchTracks();
    }
  });

  dom.nextPageBtn.addEventListener("click", () => {
    const maxPage = Math.ceil(state.totalTracks / state.limit) || 1;
    if (state.page < maxPage) {
      state.page++;
      fetchTracks();
    }
  });

  // Audio Player controls
  dom.playPauseBtn.addEventListener("click", togglePlayPause);

  dom.audioPlayer.addEventListener("timeupdate", onTimeUpdate);
  dom.audioPlayer.addEventListener("loadedmetadata", () => {
    dom.durationLabel.textContent = formatTime(dom.audioPlayer.duration || 0);
  });

  ttmlRenderer.setAudioPlayer(dom.audioPlayer);

  dom.seekSlider.addEventListener("input", () => {
    const seekTime = (dom.seekSlider.value / 100) * (dom.audioPlayer.duration || 0);
    dom.currentTimeLabel.textContent = formatTime(seekTime);
    ttmlRenderer.seekImmediate(seekTime);
  });

  dom.seekSlider.addEventListener("change", () => {
    const seekTime = (dom.seekSlider.value / 100) * (dom.audioPlayer.duration || 0);
    dom.audioPlayer.currentTime = seekTime;
    ttmlRenderer.seekImmediate(seekTime);
  });

  dom.volumeSlider.addEventListener("input", (e) => {
    dom.audioPlayer.volume = parseFloat(e.target.value);
  });

  // Style buttons (Apple Music vs Karaoke Glow)
  if (dom.styleAmlBtn && dom.styleKaraokeBtn) {
    dom.styleAmlBtn.addEventListener("click", () => {
      dom.styleAmlBtn.classList.add("active");
      dom.styleKaraokeBtn.classList.remove("active");
      ttmlRenderer.setStyle("aml");
    });

    dom.styleKaraokeBtn.addEventListener("click", () => {
      dom.styleKaraokeBtn.classList.add("active");
      dom.styleAmlBtn.classList.remove("active");
      ttmlRenderer.setStyle("karaoke");
    });
  }

  // Theme chips (active word glow color)
  dom.themeChips.forEach((chip) => {
    chip.addEventListener("click", () => {
      dom.themeChips.forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      ttmlRenderer.setAccentColor(chip.dataset.color);
    });
  });

  dom.toggleRawBtn.addEventListener("click", () => {
    const isHidden = dom.rawLyricsContainer.style.display === "none";
    dom.rawLyricsContainer.style.display = isHidden ? "block" : "none";
    if (dom.lyricsOuter) dom.lyricsOuter.style.display = isHidden ? "none" : "flex";
    dom.toggleRawBtn.textContent = isHidden ? "🎤 Pokaż karaoke" : "📄 Pokaż źródło";
  });

  // Modal actions
  dom.openSearchModalBtn.addEventListener("click", openSearchModal);
  dom.closeModalBtn.addEventListener("click", closeSearchModal);
  dom.modalExecuteSearchBtn.addEventListener("click", executeProviderSearch);

  dom.searchModal.addEventListener("click", (e) => {
    if (e.target === dom.searchModal) closeSearchModal();
  });
}

// FORMAT TIME HELPER mm:ss
function formatTime(secs) {
  if (isNaN(secs) || secs < 0) return "00:00";
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}

// 1. FETCH STATS & CHART
async function fetchStats() {
  try {
    const res = await fetch("/api/stats");
    const data = await res.json();

    dom.musicDirBadge.textContent = `Folder: ${data.music_dir}`;
    dom.statTotalTracks.textContent = data.total_tracks;
    dom.statCoveragePct.textContent = `${data.coverage_pct}% z tekstami (${data.has_lyrics_count})`;

    dom.statWordSync.textContent = data.word_sync_count;
    dom.statWordSyncPct.textContent = `${data.word_sync_pct}% biblioteki`;

    dom.statLineSync.textContent = data.line_sync_count;
    dom.statLineSyncPct.textContent = `${data.line_sync_pct}% biblioteki`;

    dom.statMissing.textContent = data.missing_count;
    dom.statMissingPct.textContent = `${data.missing_pct}% biblioteki`;

    // Render format pills
    dom.formatPills.innerHTML = "";
    if (data.format_counts) {
      for (const [fmt, count] of Object.entries(data.format_counts)) {
        const pill = document.createElement("span");
        pill.className = `badge badge-${fmt.toLowerCase()}`;
        pill.textContent = `${fmt}: ${count}`;
        dom.formatPills.appendChild(pill);
      }
    }

    renderCoverageChart(data);
  } catch (err) {
    console.error("Error fetching stats:", err);
  }
}

function renderCoverageChart(stats) {
  if (!dom.coverageChart || typeof Chart === "undefined") return;

  const ctx = dom.coverageChart.getContext("2d");
  const chartData = {
    labels: ["Word-Sync (TTML/YAML)", "Line-Sync (LRC)", "Unsynced (TXT)", "Brakujące"],
    datasets: [
      {
        data: [
          stats.word_sync_count || 0,
          stats.line_sync_count || 0,
          stats.unsynced_count || 0,
          stats.missing_count || 0,
        ],
        backgroundColor: ["#10b981", "#f59e0b", "#64748b", "#ef4444"],
        borderWidth: 0,
      },
    ],
  };

  if (state.chartInstance) {
    state.chartInstance.data = chartData;
    state.chartInstance.update();
  } else {
    state.chartInstance = new Chart(ctx, {
      type: "doughnut",
      data: chartData,
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "70%",
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (item) => ` ${item.label}: ${item.raw} (${stats.total_tracks ? Math.round((item.raw / stats.total_tracks) * 100) : 0}%)`,
            },
          },
        },
      },
    });
  }
}

// 2. FETCH TRACKS TABLE
async function fetchTracks() {
  try {
    const params = new URLSearchParams({
      page: state.page,
      limit: state.limit,
      filter: state.filter,
    });
    if (state.query) params.append("q", state.query);

    const res = await fetch(`/api/tracks?${params.toString()}`);
    const data = await res.json();

    state.totalTracks = data.total;
    renderTracksTable(data.tracks);

    // Update pagination
    const maxPage = Math.ceil(data.total / state.limit) || 1;
    dom.pageInfo.textContent = `Strona ${data.page} z ${maxPage} (${data.total} utworów)`;
    dom.prevPageBtn.disabled = data.page <= 1;
    dom.nextPageBtn.disabled = data.page >= maxPage;
  } catch (err) {
    console.error("Error fetching tracks:", err);
  }
}

function renderTracksTable(tracks) {
  dom.tracksTableBody.innerHTML = "";

  if (!tracks || tracks.length === 0) {
    dom.tracksTableBody.innerHTML = `<tr><td colspan="4" class="text-center text-dim py-4">Brak utworów spełniających kryteria.</td></tr>`;
    return;
  }

  tracks.forEach((t) => {
    const tr = document.createElement("tr");

    // Format badge
    let fmtBadge = `<span class="badge badge-missing">BRAK</span>`;
    if (t.has_lyrics && t.format) {
      fmtBadge = `<span class="badge badge-${t.format.toLowerCase()}">${t.format.toUpperCase()}</span>`;
    }

    // Sync type label
    let syncLabel = `<span class="text-dim">-</span>`;
    if (t.sync_type === "word_sync") {
      syncLabel = `<span class="text-green font-bold">Word-Sync</span>`;
    } else if (t.sync_type === "line_sync") {
      syncLabel = `<span class="text-yellow font-bold">Line-Sync</span>`;
    } else if (t.has_lyrics) {
      syncLabel = `<span class="text-dim">Unsynced</span>`;
    }

    const titleStr = t.title || t.filename;
    const artistStr = t.artist ? t.artist : (t.album ? t.album : t.relative_path);

    tr.innerHTML = `
      <td>
        <div class="track-title" title="${t.filename}">${titleStr}</div>
        <div class="track-artist">${artistStr}</div>
      </td>
      <td>${fmtBadge}</td>
      <td>${syncLabel}</td>
      <td>
        <button class="btn btn-secondary btn-sm play-btn">▶ Graj</button>
      </td>
    `;

    // Row click
    tr.querySelector(".play-btn").addEventListener("click", () => selectTrack(t));
    tr.querySelector(".track-title").addEventListener("click", () => selectTrack(t));

    dom.tracksTableBody.appendChild(tr);
  });
}

// 3. SELECT TRACK AND LOAD TTML RENDERER
async function selectTrack(track) {
  state.currentTrack = track;

  // Header update
  dom.nowPlayingTitle.textContent = track.title || track.filename;
  dom.nowPlayingMeta.textContent = `${track.artist || "Nieznany wykonawca"} • ${track.album || ""}`;
  dom.openSearchModalBtn.style.display = "inline-flex";
  dom.toggleRawBtn.style.display = "inline-flex";

  // Audio player src
  dom.audioPlayer.src = `/api/tracks/${track.id}/audio`;
  dom.audioPlayer.play().catch(() => {});
  dom.playPauseBtn.textContent = "⏸";

  // Fetch lyrics
  dom.lyricsContainer.innerHTML = `
    <div class="empty-state">
      <div class="spinner"></div>
      <div class="big">WCZYTYWANIE TEKSTU</div>
      <div class="sub">Przetwarzanie znaczników TTML i synchronizacja w toku...</div>
    </div>`;

  try {
    const res = await fetch(`/api/tracks/${track.id}/lyrics`);
    const data = await res.json();
    state.currentLyrics = data;

    dom.rawLyricsPre.textContent = data.content || "Brak pliku tekstu.";

    if (!data.has_lyrics) {
      dom.lyricsContainer.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">❌</div>
          <div class="big">BRAK TEKSTU</div>
          <div class="sub">Ten utwór nie posiada jeszcze towarzyszącego tekstu w bibliotece.</div>
          <button id="findLyricsNowBtn" class="btn btn-primary btn-sm mt-2">🔍 Znajdź tekst u dostawców</button>
        </div>
      `;
      const findBtn = document.getElementById("findLyricsNowBtn");
      if (findBtn) findBtn.addEventListener("click", openSearchModal);
      return;
    }

    ttmlRenderer.loadTTML(data.ttml_content || data.content);
  } catch (err) {
    console.error("Error loading lyrics:", err);
    dom.lyricsContainer.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">⚠️</div>
        <div class="big text-red">BŁĄD WCZYTYWANIA</div>
        <div class="sub">Nie udało się załadować tekstu utworu.</div>
      </div>`;
  }
}

// 4. HIGH-FREQUENCY TTML SYNCHRONIZATION LOOP
function startKaraokeLoop() {
  function loop() {
    if (!dom.audioPlayer.paused && state.currentLyrics && state.currentLyrics.has_lyrics) {
      ttmlRenderer.sync(dom.audioPlayer.currentTime);
    }
    state.animFrameId = requestAnimationFrame(loop);
  }
  state.animFrameId = requestAnimationFrame(loop);
}

// 5. AUDIO CONTROLS
function togglePlayPause() {
  if (dom.audioPlayer.paused) {
    dom.audioPlayer.play().then(() => {
      dom.playPauseBtn.textContent = "⏸";
    }).catch((err) => console.error("Play error:", err));
  } else {
    dom.audioPlayer.pause();
    dom.playPauseBtn.textContent = "▶";
  }
}

function onTimeUpdate() {
  const cur = dom.audioPlayer.currentTime;
  const dur = dom.audioPlayer.duration || 1;
  dom.currentTimeLabel.textContent = formatTime(cur);
  if (!dom.seekSlider.matches(":active")) {
    dom.seekSlider.value = (cur / dur) * 100;
  }
}

// 6. ALTERNATIVE VERSIONS MODAL & SEARCH
function openSearchModal() {
  if (!state.currentTrack) return;
  dom.modalArtistInput.value = state.currentTrack.artist || "";
  dom.modalTitleInput.value = state.currentTrack.title || state.currentTrack.filename;
  dom.candidatesList.innerHTML = `<div class="text-dim text-center py-4">Kliknij „Szukaj u dostawców”, aby przeszukać 13 serwisów jednocześnie.</div>`;
  dom.searchModal.style.display = "flex";
}

let currentSearchSource = null;

function closeSearchModal() {
  if (currentSearchSource) {
    currentSearchSource.close();
    currentSearchSource = null;
  }
  dom.searchModal.style.display = "none";
}

function executeProviderSearch() {
  const artist = dom.modalArtistInput.value.trim();
  const title = dom.modalTitleInput.value.trim();
  if (!artist || !title) {
    alert("Podaj wykonawcę i tytuł utworu!");
    return;
  }

  if (currentSearchSource) {
    currentSearchSource.close();
    currentSearchSource = null;
  }

  dom.modalLoadingSpinner.style.display = "flex";
  dom.candidatesList.innerHTML = "";
  let receivedCount = 0;

  const params = new URLSearchParams({
    artist: artist,
    title: title,
    duration: (state.currentTrack && state.currentTrack.duration) ? state.currentTrack.duration : 0,
    timeout: 3.5,
  });

  const evtSource = new EventSource(`/api/search-providers-stream?${params.toString()}`);
  currentSearchSource = evtSource;

  evtSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data.type === "candidate") {
        receivedCount++;
        // Instant visual feedback: hide spinner as soon as first hit arrives (200-400ms)
        dom.modalLoadingSpinner.style.display = "none";
        appendCandidateCard(data.candidate);
      } else if (data.type === "done") {
        dom.modalLoadingSpinner.style.display = "none";
        evtSource.close();
        currentSearchSource = null;
        if (receivedCount === 0) {
          dom.candidatesList.innerHTML = `<div class="text-dim text-center py-4">Nie znaleziono żadnych wersji u dostawców dla tego zapytania.</div>`;
        }
      }
    } catch (err) {
      console.error("SSE parse error:", err);
    }
  };

  evtSource.onerror = () => {
    dom.modalLoadingSpinner.style.display = "none";
    evtSource.close();
    currentSearchSource = null;
    if (receivedCount === 0) {
      fallbackFetchSearch(params);
    }
  };
}

async function fallbackFetchSearch(params) {
  try {
    dom.modalLoadingSpinner.style.display = "flex";
    const res = await fetch(`/api/search-providers?${params.toString()}`);
    const data = await res.json();
    dom.candidatesList.innerHTML = "";
    if (data.candidates && data.candidates.length > 0) {
      data.candidates.forEach(appendCandidateCard);
    } else {
      dom.candidatesList.innerHTML = `<div class="text-dim text-center py-4">Nie znaleziono żadnych wersji u dostawców dla tego zapytania.</div>`;
    }
  } catch (err) {
    console.error("Fallback search error:", err);
    dom.candidatesList.innerHTML = `<div class="text-red text-center py-4">Błąd podczas wyszukiwania u dostawców.</div>`;
  } finally {
    dom.modalLoadingSpinner.style.display = "none";
  }
}

function appendCandidateCard(cand) {
  const card = document.createElement("div");
  card.className = "candidate-card";

  let syncBadge = `<span class="badge badge-dim">${cand.sync_type}</span>`;
  if (cand.sync_type === "word_sync") {
    syncBadge = `<span class="badge badge-ttml">WORD-SYNC</span>`;
  } else if (cand.sync_type === "line_sync") {
    syncBadge = `<span class="badge badge-lrc">LINE-SYNC</span>`;
  }

  const fmtBadge = `<span class="badge badge-${cand.format.toLowerCase()}">${cand.format.toUpperCase()}</span>`;
  const scorePct = Math.round((cand.match_score || 0) * 100);

  card.innerHTML = `
    <div class="candidate-header">
      <div class="candidate-title">${cand.artist} – ${cand.title}</div>
      <div style="display:flex; gap:6px; align-items:center;">
        <span class="text-dim" style="font-size:0.8rem;">Dostawca: <strong class="text-white">${cand.provider}</strong></span>
        ${syncBadge}
        ${fmtBadge}
        <span class="badge badge-dim">${scorePct}% trafności</span>
      </div>
    </div>
    <div class="candidate-preview">${cand.preview}</div>
    <div class="candidate-actions">
      <span class="text-dim" style="font-size:0.8rem;">Czas: ${Math.round(cand.duration || 0)}s</span>
      <button class="btn btn-primary btn-sm apply-btn">✓ Zastosuj tę wersję</button>
    </div>
  `;

  card.querySelector(".apply-btn").addEventListener("click", () => applyCandidateLyrics(cand));

  // High quality word-sync versions placed at top
  if (cand.sync_type === "word_sync") {
    dom.candidatesList.prepend(card);
  } else {
    dom.candidatesList.appendChild(card);
  }
}

async function applyCandidateLyrics(candidate) {
  if (!state.currentTrack) return;

  const btn = event.target;
  btn.disabled = true;
  btn.textContent = "Zapisywanie...";

  try {
    const res = await fetch(`/api/tracks/${state.currentTrack.id}/save-lyrics`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        content: candidate.content,
        format: candidate.format,
        sync_type: candidate.sync_type,
        provider: candidate.provider,
      }),
    });

    const result = await res.json();
    if (result.success) {
      alert(`Zapisano pomyślnie wersję od dostawcy '${candidate.provider}' (${result.saved_file})!`);
      closeSearchModal();

      // Refresh current track view and stats
      selectTrack(state.currentTrack);
      fetchStats();
      fetchTracks();
    } else {
      alert("Błąd zapisu tekstu.");
    }
  } catch (err) {
    console.error("Save lyrics error:", err);
    alert("Wystąpił błąd podczas zapisywania tekstu.");
  } finally {
    btn.disabled = false;
    btn.textContent = "✓ Zastosuj tę wersję";
  }
}
