// Prelude evaluated inside the jsdom window BEFORE the real tgapp scripts.
// Stubs the Telegram bridge, the api transport (fixtures) and collects errors.
// All values live on window.* so later eval'd scripts can see them.
window.__errors = [];
window.addEventListener("error", (e) => window.__errors.push("error: " + (e.message || (e.error && e.error.message) || e.error)));
window.addEventListener("unhandledrejection", (e) => window.__errors.push("unhandledrejection: " + ((e.reason && e.reason.message) || e.reason)));
window.__apiLog = [];
window.__fx = { panelsFail: false, musicHomeFail: false, lyricsFail: false };
window.__T1 = { id: "t1", title: "Тестовая песня", artist: "Тест Артист", album: "Тест Альбом", duration: 120, cover: null, stream: "/music/stream/t1", liked: false, genre: "Rock", year: "2024" };
window.__T2 = { id: "t2", title: "Вторая песня", artist: "Тест Артист", album: "Тест Альбом", duration: 95, cover: null, stream: "/music/stream/t2", liked: false, genre: "Pop", year: "2023" };
window.__T3 = { id: "t3", title: "Третья песня", artist: "Другой Артист", album: "", duration: 200, cover: null, stream: "/music/stream/t3", liked: true, genre: "Rock", year: "2024" };
window.__LYRICS = { synced: [[0, "こんにちは 世界"], [2, "사랑해요"], [4, "Hello world", [[4, "Hello", 4.6], [4.6, "world", 5.2]]]],
  plain: null, source: "lrclib", source_name: "LRCLib", offset: 0, general_offset: 0, choice: 0,
  alts: [{ index: 0, source: "lrclib", name: "LRCLib", synced: true, words: true, duration: 120, title: "Тестовая песня", artist: "Тест Артист", lines: 3 },
         { index: 1, source: "netease", name: "NetEase", synced: true, words: false, duration: 121, title: "Тестовая песня", artist: "Тест Артист", lines: 3 }],
  instrumental: false, words: true, cached: true };
window.__fixture = function (name, body) {
  window.__apiLog.push(name);
  const T1 = window.__T1, T2 = window.__T2, T3 = window.__T3;
  if (name === "auth") return { token: "tok", expires: 4102444800, user: { id: 1 }, lang: "ru" };
  if (name === "music.home") { if (window.__fx.musicHomeFail) { const e = new Error("boom"); e.code = "offline"; throw e; } return { liked: [], added: [T1, T2], playlists: [], stats: { tracks: 3, minutes: 6, recent: [T1] }, player: { online: false }, pc_id: "pc1", look: {} }; }
  if (name === "music.list") return { tracks: [T1, T2, T3], total: 3 };
  if (name === "music.lyrics") { if (window.__fx.lyricsFail) throw new Error("no lyrics"); return JSON.parse(JSON.stringify(window.__LYRICS)); }
  if (name === "music.remote") return { online: false, available: false };
  if (name === "music.playlists") return { playlists: [] };
  if (name === "music.playlist") return { playlist: { id: "p1", name: "Мой плейлист", tracks: [T1, T2], count: 2 } };
  if (name === "music.browse") return { tracks: [T1, T2], albums: [], name: (body && body.name) || "", cover: null, accent: "" };
  if (name === "music.search") return { results: [] };
  if (name === "music.jobs") return { jobs: [] };
  if (name === "music.detect") return { source: "youtube", kind: "track", title: "x" };
  if (name === "music.like" || name === "music.sync" || name === "music.report" || name === "music.download" || name === "music.cancel") return { ok: true };
  if (name === "panels.list") { if (window.__fx.panelsFail) throw new Error("panels boom"); return { telegram_ready: true, settings: {}, panels: [
    { id: "p-clock", kind: "clock", title: "Часы", status: "ok", eligible: false, refreshing: false, local: true, sub: { on: false }, card: {} },
    { id: "p-wx", kind: "weather", title: "Погода", status: "ok", eligible: false, refreshing: false, local: false, sub: { on: false }, card: { metric: { value: "+18", unit: "°C", caption: "Ясно" }, subtitle: "Москва", updated: 1790000000 } },
    { id: "p-todo", kind: "todo", title: "Задачи", status: "ok", eligible: false, refreshing: false, local: true, sub: { on: false }, card: { tasks: [{ title: "Купить молоко", due: "2026-10-31" }] } },
    { id: "p-timers", kind: "timers", title: "Таймеры", status: "ok", eligible: false, refreshing: false, local: true, sub: { on: false }, card: {} },
    { id: "p-cd", kind: "countdown", title: "Отпуск", status: "ok", eligible: false, refreshing: false, local: false, sub: { on: false }, card: { countdown: { days_left: 5, label: "Отпуск" } } }
  ] }; }
  if (name === "panels.subscribe") return { sub: { on: true, mode: "changes", quiet: "silent", at: "09:00" } };
  if (name === "panels.refresh") return { result: "local" };
  return {};
};
window.fetch = async function (url, opts) {
  const u = String(url || "");
  if (u.startsWith("api/")) {
    const name = u.slice(4);
    try {
      const body = opts && opts.body ? JSON.parse(opts.body) : {};
      const data = window.__fixture(name, body);
      return { ok: true, status: 200, json: async () => data };
    } catch (e) {
      return { ok: false, status: 500, json: async () => ({ error: e.message || "err", code: e.code || "" }) };
    }
  }
  return { ok: false, status: 404, json: async () => ({}) };
};
window.Telegram = { WebApp: {
  initData: "", initDataUnsafe: { user: { id: 1, language_code: "ru" } }, version: "8.0", platform: "android",
  themeParams: {}, colorScheme: "dark", isExpanded: true, viewportHeight: 800, viewportStableHeight: 800,
  safeAreaInset: { top: 0, bottom: 0, left: 0, right: 0 }, contentSafeAreaInset: { top: 0, bottom: 0, left: 0, right: 0 },
  isFullscreen: false, isVersionAtLeast: () => true, onEvent: () => {}, offEvent: () => {},
  ready: () => {}, expand: () => {}, disableVerticalSwipes: () => {}, setHeaderColor: () => {}, setBackgroundColor: () => {}, setBottomBarColor: () => {},
  openLink: () => {}, HapticFeedback: { impactOccurred: () => {}, notificationOccurred: () => {}, selectionChanged: () => {} },
  BackButton: { isVisible: false, show() {}, hide() {}, onClick() {}, offClick() {} },
  CloudStorage: null
} };
window.MemuBridge = { ready: Promise.resolve({ app: window.Telegram.WebApp, official: false, params: {} }), parseQuery: () => ({}), versionAtLeast: () => true };
