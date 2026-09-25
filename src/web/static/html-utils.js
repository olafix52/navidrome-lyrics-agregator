// HTML escaping helpers for values interpolated into innerHTML templates.
// Lyrics, titles and attribution come from third-party / crowd-sourced providers
// and audio tags, so they must never be inserted as raw markup.

const HTML_ESCAPES = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

export function escapeHtml(value) {
  if (value === null || value === undefined) return "";
  return String(value).replace(/[&<>"']/g, (ch) => HTML_ESCAPES[ch]);
}

// Only allow absolute http(s) links; anything else (javascript:, data:, ...) yields "".
export function safeUrl(value) {
  if (!value) return "";
  try {
    const url = new URL(String(value), window.location.origin);
    if (url.protocol === "http:" || url.protocol === "https:") {
      return url.href;
    }
  } catch (_) {
    // invalid URL
  }
  return "";
}
