(function (root) {
  function safeHttpUrl(value, origin = root.location?.origin) {
    try {
      if (typeof value !== "string" || !value.trim()) return "";
      const url = new URL(String(value), origin);
      if ((url.protocol !== "http:" && url.protocol !== "https:")
          || url.username || url.password) return "";
      return url.href;
    } catch {
      return "";
    }
  }

  function safeSameOriginPath(value, origin = root.location?.origin) {
    try {
      if (typeof value !== "string" || !value.trim()) return "";
      const base = new URL(origin);
      const url = new URL(String(value), base);
      if (url.origin !== base.origin) return "";
      return `${url.pathname}${url.search}${url.hash}`;
    } catch {
      return "";
    }
  }

  const api = { safeHttpUrl, safeSameOriginPath };
  root.VskrUrlSafety = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window === "undefined" ? globalThis : window);
