/**
 * TTML Karaoke Renderer for Navidrome Lyrics Aggregator
 * Based on ToxiPlays TTML Renderer (https://github.com/ToxiPlays/ToxiPlays.github.io/tree/main/ttmlrenderer)
 * Supports Apple Music (AMLL) styling, word-by-word jitter/glow, long-word letter-bloom,
 * instrumental break countdown bars, duets (v1/v2), ad-libs (x-bg), and interactive seeking.
 */

export class TTMLRenderer {
  constructor(containerId = "lyrics-container") {
    this.containerId = containerId;
    this.audioPlayer = null;
    this.currentStyle = "aml"; // 'aml' (Apple Music) or 'karaoke' (Glow)
    this.accentColor = "#e8f440";
    
    this.state = {
      spans: [],      // { el, begin, end, duration, isLong, lineEl }
      lines: [],      // { el, begin, end, agent, isAdlib, skipRetroactive }
      breakBars: [],  // { el, fillEl, start, end, gap }
      activeSpanSet: new Set(),
      activeLineSet: new Set(),
      rafId: null,
      isLoaded: false,
    };
  }

  get container() {
    return document.getElementById(this.containerId);
  }

  setAudioPlayer(player) {
    this.audioPlayer = player;
  }

  parseTime(str) {
    if (!str) return 0;
    const parts = str.trim().split(":");
    let secs = 0;
    if (parts.length === 3) {
      secs = (+parts[0]) * 3600 + (+parts[1]) * 60 + parseFloat(parts[2]);
    } else if (parts.length === 2) {
      secs = (+parts[0]) * 60 + parseFloat(parts[1]);
    } else {
      secs = parseFloat(parts[0]);
    }
    return isNaN(secs) ? 0 : secs;
  }

  formatTime(s) {
    if (isNaN(s) || s < 0) return "0:00";
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, "0")}`;
  }

  hexToRGBA(hex, alpha = 0.45) {
    hex = hex.replace("#", "");
    if (hex.length === 3) hex = hex[0] + hex[0] + hex[1] + hex[1] + hex[2] + hex[2];
    const r = parseInt(hex.substring(0, 2), 16) || 232;
    const g = parseInt(hex.substring(2, 4), 16) || 244;
    const b = parseInt(hex.substring(4, 6), 16) || 64;
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }

  setAccentColor(color) {
    this.accentColor = color;
    document.documentElement.style.setProperty("--active-word", color);
    document.documentElement.style.setProperty("--accent", color);
    document.documentElement.style.setProperty("--active-glow", this.hexToRGBA(color, 0.45));
  }

  setStyle(styleName) {
    this.currentStyle = styleName; // 'aml' or 'karaoke'
    const isAml = styleName === "aml";
    const outer = this.container?.closest(".lyrics-outer") || this.container?.parentElement;
    if (outer) outer.classList.toggle("apple-music", isAml);
    if (this.container) {
      this.container.querySelectorAll(".lyric-line").forEach((el) => {
        el.classList.toggle("apple-music", isAml);
      });
    }
  }

  stripParensFromTokens(tokenList) {
    const result = tokenList.map((t) => ({ ...t }));
    for (let i = 0; i < result.length; i++) {
      const stripped = result[i].text.replace(/^\s*\(\s*/, "");
      if (stripped !== result[i].text) {
        result[i].text = stripped;
        break;
      }
    }
    for (let i = result.length - 1; i >= 0; i--) {
      const stripped = result[i].text.replace(/\s*\)\s*$/, "");
      if (stripped !== result[i].text) {
        result[i].text = stripped;
        break;
      }
    }
    result.forEach((t) => {
      t.text = t.text.replace(/[()]/g, "");
    });
    return result;
  }

  loadTTML(xmlString, attributionData = null) {
    const container = this.container;
    if (!container) return;

    container.innerHTML = "";
    this.state.spans = [];
    this.state.lines = [];
    this.state.breakBars = [];
    this.state.activeSpanSet.clear();
    this.state.activeLineSet.clear();
    this.state.isLoaded = false;

    if (!xmlString || !xmlString.trim()) {
      container.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">❌</div>
          <div class="big">BRAK TEKSTU</div>
          <div class="sub">Ten utwór nie posiada jeszcze towarzyszącego tekstu.</div>
        </div>`;
      return;
    }

    const parser = new DOMParser();
    const doc = parser.parseFromString(xmlString, "text/xml");

    const xmlErr = doc.querySelector("parsererror");
    if (xmlErr) {
      container.innerHTML = `
        <div class="empty-state">
          <div class="big">BŁĄD XML</div>
          <div class="sub">Nie udało się zinterpretować pliku jako XML.<br><em>${xmlErr.textContent.split("\n")[0]}</em></div>
        </div>`;
      return;
    }

    const ns = "http://www.w3.org/ns/ttml";
    const ttmNS = "http://www.w3.org/ns/ttml#metadata";

    let pEls = doc.getElementsByTagNameNS(ns, "p");
    if (pEls.length === 0) pEls = doc.getElementsByTagName("p");

    if (pEls.length === 0) {
      container.innerHTML = `
        <div class="empty-state">
          <div class="big">BRAK LINII TEKSTU</div>
          <div class="sub">Plik TTML nie zawiera żadnych wersów piosenki.</div>
        </div>`;
      return;
    }

    const isAml = this.currentStyle === "aml";

    Array.from(pEls).forEach((p) => {
      const agent = p.getAttributeNS(ttmNS, "agent") || p.getAttribute("ttm:agent") || "v1";
      const lineBegin = this.parseTime(p.getAttribute("begin"));
      const lineEnd = this.parseTime(p.getAttribute("end"));

      const tokens = [];

      const collectTokens = (node, forceAdlib) => {
        node.childNodes.forEach((child) => {
          if (child.nodeType === Node.TEXT_NODE) {
            if (child.textContent) tokens.push({ type: "text", text: child.textContent, isAdlib: !!forceAdlib });
          } else if (child.nodeType === Node.ELEMENT_NODE && (child.localName === "span" || child.tagName.toLowerCase() === "span")) {
            const b = child.getAttribute("begin");
            const e = child.getAttribute("end");
            const role = child.getAttributeNS(ttmNS, "role") || child.getAttribute("ttm:role") || "";
            const isXBg = role === "x-bg";
            if (role && role !== "x-bg") return;
            if (isXBg) {
              collectTokens(child, true);
            } else if (b && e) {
              const begin = this.parseTime(b);
              const end = this.parseTime(e);
              const text = child.textContent;
              tokens.push({ type: "span", begin, end, wordDuration: Math.max(0.01, end - begin), text, isAdlib: !!forceAdlib });
            } else {
              collectTokens(child, forceAdlib);
            }
          }
        });
      };

      collectTokens(p, false);

      const hasXBgAdlib = tokens.some((t) => t.isAdlib);
      let mainTokens, adlibTokens;

      if (hasXBgAdlib) {
        mainTokens = tokens.filter((t) => !t.isAdlib);
        adlibTokens = tokens.filter((t) => t.isAdlib);
        while (mainTokens.length && mainTokens[mainTokens.length - 1].type === "text") mainTokens.pop();
      } else {
        const fullText = tokens.map((t) => t.text).join("").trim();
        const entireLineIsAdlib = fullText.startsWith("(");
        let splitIdx = -1;
        for (let i = 0; i < tokens.length; i++) {
          const tok = tokens[i];
          if (tok.text && tok.text.includes("(")) {
            splitIdx = i;
            break;
          }
        }
        if (entireLineIsAdlib) {
          mainTokens = [];
          adlibTokens = tokens;
        } else if (splitIdx >= 0) {
          mainTokens = tokens.slice(0, splitIdx);
          adlibTokens = tokens.slice(splitIdx);
          while (mainTokens.length && mainTokens[mainTokens.length - 1].type === "text") mainTokens.pop();
        } else {
          mainTokens = tokens;
          adlibTokens = [];
        }
      }

      const makeSpanEl = (tok) => {
        const spanEl = document.createElement("span");
        spanEl.className = "lyric-span";
        spanEl.dataset.begin = tok.begin;
        spanEl.dataset.end = tok.end;
        spanEl.dataset.duration = tok.wordDuration;
        spanEl.textContent = /\S/.test(tok.text) ? tok.text.trimEnd() : tok.text;
        spanEl.style.setProperty("--word-dur", tok.wordDuration.toFixed(3) + "s");

        spanEl.addEventListener("click", (ev) => {
          ev.stopPropagation();
          this.seekTo(tok.begin);
        });

        this.state.spans.push({
          el: spanEl,
          begin: tok.begin,
          end: tok.end,
          duration: tok.wordDuration,
          lineEl: null,
        });
        return spanEl;
      };

      const appendSpanWithTrail = (parent, tok) => {
        const el = makeSpanEl(tok);
        parent.appendChild(el);
        if (/\S/.test(tok.text)) {
          const trail = tok.text.match(/\s+$/);
          if (trail) parent.appendChild(document.createTextNode(trail[0]));
        }
      };

      const appendGroupWithTrail = (parent, group) => {
        const lastTok = group[group.length - 1];
        if (group.length === 1) {
          appendSpanWithTrail(parent, group[0]);
        } else {
          const wrapper = document.createElement("span");
          wrapper.className = "word-group";
          group.forEach((t) => wrapper.appendChild(makeSpanEl(t)));
          parent.appendChild(wrapper);
          if (/\S/.test(lastTok.text)) {
            const trail = lastTok.text.match(/\s+$/);
            if (trail) parent.appendChild(document.createTextNode(trail[0]));
          }
        }
      };

      const buildLine = (tokenList, isAdlib) => {
        const lineEl = document.createElement("div");
        lineEl.className = "lyric-line" + (isAdlib ? " adlib" : "") + (isAml ? " apple-music" : "");
        lineEl.dataset.agent = agent;

        const spanToks = tokenList.filter((t) => t.type === "span");
        const lb = spanToks.length ? spanToks[0].begin : lineBegin;
        const le = spanToks.length ? spanToks[spanToks.length - 1].end : lineEnd;
        lineEl.dataset.begin = lb;
        lineEl.dataset.end = le;

        lineEl.addEventListener("click", () => {
          this.seekTo(lb);
        });

        const processedTokens = tokenList;

        let i = 0;
        while (i < processedTokens.length) {
          const tok = processedTokens[i];
          if (tok.type === "text") {
            lineEl.appendChild(document.createTextNode(tok.text));
            i++;
          } else {
            const group = [tok];
            let j = i + 1;
            if (!/\s$/.test(tok.text)) {
              while (j < processedTokens.length) {
                const next = processedTokens[j];
                if (next.type === "text") {
                  if (/\S/.test(next.text)) break;
                  const afterSpace = processedTokens[j + 1];
                  if (!afterSpace || afterSpace.type !== "span") break;
                  break;
                } else {
                  group.push(next);
                  j++;
                  if (/\s$/.test(next.text)) break;
                }
              }
            }

            if (group.length === 1) {
              appendSpanWithTrail(lineEl, group[0]);
            } else {
              appendGroupWithTrail(lineEl, group);
            }
            i = j;
          }
        }

        return { lineEl, begin: lb, end: le };
      };

      if (mainTokens.some((t) => t.type === "span")) {
        const { lineEl, begin, end } = buildLine(mainTokens, false);
        lineEl.querySelectorAll(".lyric-span").forEach((el) => {
          const s = this.state.spans.find((s) => s.el === el && s.lineEl === null);
          if (s) s.lineEl = lineEl;
        });
        container.appendChild(lineEl);
        this.state.lines.push({ el: lineEl, begin, end, agent, isAdlib: false });
      }

      if (adlibTokens.some((t) => t.type === "span")) {
        const { lineEl, begin, end } = buildLine(adlibTokens, true);
        lineEl.querySelectorAll(".lyric-span").forEach((el) => {
          const s = this.state.spans.find((s) => s.el === el && s.lineEl === null);
          if (s) s.lineEl = lineEl;
        });
        const hasOverlap = adlibTokens.some((t) => t.type === "span" && t.text.includes(";"));
        container.appendChild(lineEl);
        this.state.lines.push({ el: lineEl, begin, end, agent, isAdlib: true, skipRetroactive: hasOverlap });
      }
    });

    // Detect median word duration and tag long words for letter blooming
    if (this.state.spans.length > 0) {
      const durations = this.state.spans.map((s) => s.duration).sort((a, b) => a - b);
      const median = durations[Math.floor(durations.length / 2)] || 0.3;
      const threshold = Math.max(0.75, median * 4.0);
      this.state.spans.forEach((s) => {
        s.isLong = s.duration >= threshold;
      });
    }

    // Instrumental break countdown bars (gaps >= 5 seconds)
    this.state.breakBars = [];
    const BREAK_THRESHOLD = 5.0;
    for (let i = 0; i < this.state.lines.length - 1; i++) {
      const gap = this.state.lines[i + 1].begin - this.state.lines[i].end;
      if (gap >= BREAK_THRESHOLD) {
        const barEl = document.createElement("div");
        barEl.className = "break-bar";
        const secs = Math.round(gap);
        barEl.innerHTML = `
          <div class="break-bar-label">${secs}s</div>
          <div class="break-bar-track"><div class="break-bar-fill"></div></div>
        `;
        this.state.lines[i].el.after(barEl);
        this.state.breakBars.push({
          el: barEl,
          fillEl: barEl.querySelector(".break-bar-fill"),
          start: this.state.lines[i].end,
          end: this.state.lines[i + 1].begin,
          gap,
        });
      }
    }

    // Songwriters metadata credit at the end
    const songwriters = Array.from(doc.getElementsByTagName("songwriter"))
      .map((el) => el.textContent.trim())
      .filter(Boolean);
    if (songwriters.length > 0) {
      const credit = document.createElement("div");
      credit.className = "songwriter-credit";
      credit.textContent = "Autorzy / Twórcy: " + songwriters.join(", ");
      container.appendChild(credit);
    }

    // Attribution & Provider credit footer (Spicy Lyrics attribution compliance)
    let provider = attributionData?.provider;
    let source = attributionData?.source;
    let maker = attributionData?.maker;
    let uploader = attributionData?.uploader;
    let copyrightText = attributionData?.copyright_text;

    const attrEl = doc.getElementsByTagName("attribution")[0]
      || Array.from(doc.getElementsByTagName("*")).find((e) => e.localName === "attribution");
    if (attrEl) {
      provider = attrEl.getAttribute("provider") || provider;
      source = attrEl.getAttribute("source") || source;
      const mEl = Array.from(attrEl.children).find((c) => c.localName === "maker");
      if (mEl) {
        maker = {
          username: mEl.getAttribute("username") || "",
          url: mEl.getAttribute("url") || "",
        };
      }
      const uEl = Array.from(attrEl.children).find((c) => c.localName === "uploader");
      if (uEl) {
        uploader = {
          username: uEl.getAttribute("username") || "",
          url: uEl.getAttribute("url") || "",
        };
      }
    }

    if (!copyrightText) {
      const copyEl = doc.getElementsByTagName("copyright")[0]
        || doc.getElementsByTagName("ttm:copyright")[0]
        || Array.from(doc.getElementsByTagName("*")).find((e) => e.localName === "copyright");
      if (copyEl) copyrightText = copyEl.textContent.trim();
    }

    if (provider || copyrightText || source || maker || uploader) {
      const attrContainer = document.createElement("div");
      attrContainer.className = "lyrics-attribution";

      const parts = [];
      const pName = provider || "Spicy Lyrics";
      if (source && source.toLowerCase() !== "spicy_lyrics" && source.toLowerCase() !== "unknown") {
        const formattedSrc = source.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
        parts.push(`<span class="attr-provider">Dostawca tekstu: <strong>${pName} (${formattedSrc})</strong></span>`);
      } else {
        parts.push(`<span class="attr-provider">Dostawca tekstu: <strong>${pName}</strong></span>`);
      }

      if (source === "spicy_lyrics" || (!source && (maker || uploader))) {
        if (maker && maker.username) {
          const mLabel = maker.url
            ? `<a href="${maker.url}" target="_blank" rel="noopener noreferrer" class="attr-link">${maker.username}</a>`
            : `<strong>${maker.username}</strong>`;
          parts.push(`<span class="attr-maker">Synchronizacja: ${mLabel}</span>`);
        }
        if (uploader && uploader.username) {
          const uLabel = uploader.url
            ? `<a href="${uploader.url}" target="_blank" rel="noopener noreferrer" class="attr-link">${uploader.username}</a>`
            : `<strong>${uploader.username}</strong>`;
          parts.push(`<span class="attr-uploader">Przesłane przez: ${uLabel}</span>`);
        }
      }

      attrContainer.innerHTML = parts.join(` <span class="attr-bullet">·</span> `);
      container.appendChild(attrContainer);
    }

    this.state.isLoaded = true;
    this.setStyle(this.currentStyle);
    this.seekImmediate(this.audioPlayer?.currentTime || 0);
  }

  seekTo(timeInSeconds) {
    if (this.audioPlayer) {
      this.audioPlayer.currentTime = timeInSeconds;
      if (this.audioPlayer.paused) {
        this.audioPlayer.play().catch(() => {});
      }
    }
    this.seekImmediate(timeInSeconds);
  }

  seekImmediate(t) {
    this.state.activeSpanSet.clear();
    this.state.activeLineSet.clear();

    this.state.spans.forEach((s) => {
      s.el.classList.remove("active", "long-word");
      if (s.el.querySelector(".lyric-letter")) {
        s.el.textContent = s.el.textContent;
      }
      if (s.end <= t) {
        s.el.classList.add("past");
      } else {
        s.el.classList.remove("past");
      }
    });

    this.state.lines.forEach((l) => l.el.classList.remove("active-line"));
    this.state.breakBars.forEach((b) => {
      if (t < b.start) {
        b.fillEl.style.width = "0%";
        b.el.style.opacity = "0.3";
      } else if (t > b.end) {
        b.fillEl.style.width = "100%";
        b.el.style.opacity = "0.3";
      } else {
        const pct = ((t - b.start) / b.gap) * 100;
        b.fillEl.style.width = `${pct.toFixed(1)}%`;
        b.el.style.opacity = "1";
      }
    });

    this.sync(t);
  }

  sync(t) {
    if (!this.state.isLoaded) return;

    // 1. Spans synchronization (word-by-word)
    const newActiveSpans = new Set();
    for (let i = 0; i < this.state.spans.length; i++) {
      if (t >= this.state.spans[i].begin && t < this.state.spans[i].end) {
        newActiveSpans.add(i);
      }
    }

    // Handle spans leaving active
    for (const i of this.state.activeSpanSet) {
      if (!newActiveSpans.has(i)) {
        const s = this.state.spans[i];
        s.el.classList.remove("active", "long-word");
        if (t >= s.end) {
          s.el.classList.add("past");
          if (s.el.querySelector(".lyric-letter")) {
            s.el.textContent = s.el.textContent;
          }
        }
      }
    }

    // Handle spans becoming newly active
    for (const i of newActiveSpans) {
      if (!this.state.activeSpanSet.has(i)) {
        const s = this.state.spans[i];
        s.el.classList.add("active");
        s.el.classList.remove("past");

        // Dynamic Letter Bloom for long syllables
        if (s.isLong) {
          s.el.classList.add("long-word");
          const text = s.el.textContent;
          s.el.innerHTML = "";
          [...text].forEach((char, idx) => {
            const letterEl = document.createElement("span");
            letterEl.className = "lyric-letter";
            letterEl.textContent = char;
            letterEl.style.setProperty("--letter-index", idx);
            s.el.appendChild(letterEl);
          });
        }

        // Retroactive marking for past words on current line
        const lineEntry = this.state.lines.find((l) => l.el === s.lineEl);
        if (!lineEntry || !lineEntry.skipRetroactive) {
          for (let j = 0; j < i; j++) {
            if (this.state.spans[j].lineEl === s.lineEl && !newActiveSpans.has(j)) {
              this.state.spans[j].el.classList.remove("active", "long-word");
              this.state.spans[j].el.classList.add("past");
            }
          }
        }
      }
    }
    this.state.activeSpanSet = newActiveSpans;

    // 2. Lines synchronization and auto-scroll
    const newActiveLines = new Set();
    for (let i = 0; i < this.state.lines.length; i++) {
      if (t >= this.state.lines[i].begin && t < this.state.lines[i].end) {
        newActiveLines.add(i);
      }
    }

    for (const i of this.state.activeLineSet) {
      if (!newActiveLines.has(i)) {
        this.state.lines[i].el.classList.remove("active-line");
      }
    }

    let scrollTarget = null;
    for (const i of newActiveLines) {
      if (!this.state.activeLineSet.has(i)) {
        this.state.lines[i].el.classList.add("active-line");
        if (scrollTarget === null) {
          const isAdlib = this.state.lines[i].el.classList.contains("adlib");
          const hasActiveNonAdlib = [...newActiveLines].some(
            (j) => j !== i && !this.state.lines[j].el.classList.contains("adlib")
          );
          if (!isAdlib || !hasActiveNonAdlib) {
            scrollTarget = this.state.lines[i].el;
          }
        }
      }
    }

    if (scrollTarget && this.container) {
      const container = this.container;
      const containerRect = container.getBoundingClientRect();
      const lineRect = scrollTarget.getBoundingClientRect();
      const offset = lineRect.top - containerRect.top - (container.clientHeight / 2) + (lineRect.height / 2);
      container.scrollBy({ top: offset, behavior: "smooth" });
    }
    this.state.activeLineSet = newActiveLines;

    // 3. Break countdown bars
    for (const bar of this.state.breakBars) {
      if (t >= bar.start && t <= bar.end) {
        const pct = ((t - bar.start) / bar.gap) * 100;
        bar.fillEl.style.width = pct.toFixed(2) + "%";
        bar.el.style.opacity = "1";
      } else if (t > bar.end) {
        bar.fillEl.style.width = "100%";
        bar.el.style.opacity = "0.3";
      } else {
        bar.fillEl.style.width = "0%";
        bar.el.style.opacity = "0.3";
      }
    }
  }
}
