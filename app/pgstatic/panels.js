

"use strict";
const MemuPanels = (() => {

  const PLACES = ["appbar", "top", "composer", "home", "left", "right", "sidebar", "sidebar_bottom"];
  const LAY = () => (window.MemuPanelsLayout && window.MemuPanelsLayout.ready ? window.MemuPanelsLayout : null);

  const EMBED = !!window.MEMU_PANELS_EMBED;
  const subscribers = new Set();
  const state = {
    panels: [], dismissed: [], settings: {}, templates: [], loaded: false, loading: null,
    timer: 0, fastUntil: 0, menu: null, pop: null, modal: null, drag: null, lastJson: "",
    seen: new Set(), langSynced: "", geoTimer: 0,
    live: null, cpuHist: [], liveTimer: 0, floors: {}, presets: [1, 5, 15, 30, 60, 180, 360, 720, 1440], detected: null,

    liveHist: {}, liveMs: 2000, liveProcs: null, liveBusy: false, timers: null, timersTimer: 0, todoUi: {}, noteEdit: {},
    schema: {}, floorReasons: {}, gridRO: null, gridObserved: null,
  };

  const en = () => (typeof PG_LANG !== "undefined" ? PG_LANG === "en" : localStorage.getItem("memu_lang") === "en");
  const t = (ru, english) => (en() ? english : ru);
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const $q = (sel, root = document) => root.querySelector(sel);
  const $$q = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const byId = (id) => state.panels.find((p) => p.id === id) || state.dismissed.find((p) => p.id === id) || null;
  const motionOff = () => document.documentElement.dataset.motion === "off" || document.body.classList.contains("reduce-motion")
    || matchMedia("(prefers-reduced-motion: reduce)").matches;

  async function call(path, opts = {}) {
    if (typeof api === "function") return api(path, opts);
    const resp = await fetch(path, { method: opts.method || "GET", headers: { "Content-Type": "application/json" },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
    return data;
  }
  const notify = (text, kind = "ok") => {
    if (typeof toast === "function") toast(text, kind);
    else if (EMBED && window.MEMU_PANELS_HOST && window.MEMU_PANELS_HOST.toast) window.MEMU_PANELS_HOST.toast(text, kind === "err" ? "error" : kind);
    else if (EMBED && window.MemuQuick) window.MemuQuick.toast(text, kind === "err" ? "error" : kind);
  };

  const SUN = '<circle class="sun-fill" cx="12" cy="12" r="4.2"/><path class="sun" d="M12 2.6v2.2M12 19.2v2.2M2.6 12h2.2M19.2 12h2.2M5.4 5.4l1.5 1.5M17.1 17.1l1.5 1.5M5.4 18.6l1.5-1.5M17.1 6.9l1.5-1.5"/>';
  const CLOUD = (y = 0) => `<path class="cloud" transform="translate(0 ${y})" d="M7.2 19h9.6a4.1 4.1 0 0 0 .5-8.2 5.6 5.6 0 0 0-10.6 1.4A3.4 3.4 0 0 0 7.2 19z"/>`;
  const ICONS = {
    clear: SUN,
    "mostly-clear": SUN,
    "clear-night": '<path class="moon" d="M19.5 14.6A7.6 7.6 0 0 1 9.4 4.5a7.6 7.6 0 1 0 10.1 10.1z"/>',
    partly: '<g transform="translate(-3 -3) scale(.8)"><circle class="sun-fill" cx="12" cy="12" r="4.2"/><path class="sun" d="M12 3v2M3 12h2M5.6 5.6 7 7M18.4 5.6 17 7M12 19v2"/></g>' + CLOUD(1.5),
    "partly-night": '<path class="moon" transform="translate(-4 -4) scale(.75)" d="M19.5 14.6A7.6 7.6 0 0 1 9.4 4.5a7.6 7.6 0 1 0 10.1 10.1z"/>' + CLOUD(1.5),
    cloudy: '<path class="cloud" opacity=".55" transform="translate(3 -3.5) scale(.82)" d="M7.2 19h9.6a4.1 4.1 0 0 0 .5-8.2 5.6 5.6 0 0 0-10.6 1.4A3.4 3.4 0 0 0 7.2 19z"/>' + CLOUD(1),
    fog: CLOUD(-3) + '<path d="M4 19.5h12M7 22h13"/>',
    drizzle: CLOUD(-3) + '<path class="rain" d="M9 19.5v1.2M13 19.5v1.2M17 19.5v1.2"/>',
    rain: CLOUD(-3) + '<path class="rain" d="m9 18.5-1.2 3M13 18.5l-1.2 3M17 18.5l-1.2 3"/>',
    snow: CLOUD(-3) + '<path class="snow" d="M9 19.2v2.6M7.8 20.5h2.4M15 19.2v2.6M13.8 20.5h2.4"/>',
    storm: CLOUD(-3) + '<path class="bolt" d="m12.5 16.5-2.2 3.4h2.4l-1.4 3 3.6-4.4h-2.5l1.3-2z"/>',
    chart: '<path d="M3.5 3.5v17h17"/><path d="m7 15 4-4.5 3 3 5.5-6.5"/>',
    coin: '<circle cx="12" cy="12" r="8.5"/><path d="M14.8 9.2c-.5-.9-1.6-1.4-2.8-1.4-1.6 0-2.8.8-2.8 2.1 0 2.9 5.8 1.4 5.8 4.3 0 1.3-1.2 2.2-3 2.2-1.3 0-2.4-.6-2.9-1.5M12 6v1.8M12 16.4v1.8"/>',
    btc: '<circle cx="12" cy="12" r="8.5"/><path d="M9.5 7.5h3.6a2 2 0 0 1 0 4H9.5zm0 4h4.1a2.2 2.2 0 0 1 0 4.5H9.5zM9.5 7.5V16M11 6v1.5M11 16v1.5M13 6v1.5M13 16v1.5"/>',
    news: '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M7 8.5h10M7 12h10M7 15.5h6"/>',
    trophy: '<path d="M8 4.5h8v4a4 4 0 0 1-8 0zM8 6.5H5a2.5 2.5 0 0 0 3 3M16 6.5h3a2.5 2.5 0 0 1-3 3M12 12.5v3.5M8.5 19.5h7M9.5 19.5l.6-3.5h3.8l.6 3.5"/>',
    calendar: '<rect x="3.5" y="5" width="17" height="15" rx="2.5"/><path d="M3.5 9.5h17M8 3v4M16 3v4"/>',
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    globe: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.3 2.4 3.4 5.2 3.4 8.5s-1.1 6.1-3.4 8.5c-2.3-2.4-3.4-5.2-3.4-8.5S9.7 5.9 12 3.5z"/>',
    bolt: '<path d="M13 2.5 4.5 13.5h6.5l-1 8 8.5-11h-6.5z"/>',
    heart: '<path d="M12 20s-7.5-4.5-7.5-10A4.3 4.3 0 0 1 12 7.4 4.3 4.3 0 0 1 19.5 10c0 5.5-7.5 10-7.5 10z"/>',
    star: '<path d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/>',
    bell: '<path d="M18 9.5a6 6 0 0 0-12 0c0 6-2.5 7.5-2.5 7.5h17S18 15.5 18 9.5M10.3 20a2 2 0 0 0 3.4 0"/>',
    home: '<path d="M4 10.5 12 4l8 6.5V19a1 1 0 0 1-1 1h-4.5v-5.5h-5V20H5a1 1 0 0 1-1-1z"/>',
    cart: '<path d="M3.5 4.5h2.2l2 10h10.1l1.9-7H6.6"/><circle cx="9" cy="19" r="1.3"/><circle cx="17" cy="19" r="1.3"/>',
    book: '<path d="M5 4.5A1.5 1.5 0 0 1 6.5 3H19v15H6.5A1.5 1.5 0 0 0 5 19.5zM5 19.5A1.5 1.5 0 0 0 6.5 21H19"/>',
    music: '<path d="M9 18V5.5l11-2V16"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="17.5" cy="16" r="2.5"/>',
    film: '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M7.5 4.5v15M16.5 4.5v15M3.5 9h4M3.5 15h4M16.5 9h4M16.5 15h4"/>',
    plane: '<path d="M10.5 13.5 3.5 11l1.5-1.5 7 1 4-4.5c1-1 2.6-1.4 3-1s0 2-1 3l-4.5 4 1 7L13 20.5l-2.5-7z"/>',
    car: '<path d="M5 16.5h14v-4l-2-4.5H7l-2 4.5zM5 12.5h14"/><circle cx="8" cy="16.5" r="1.6"/><circle cx="16" cy="16.5" r="1.6"/>',
    fire: '<path d="M12 21c-3.6 0-6-2.4-6-5.8 0-3.6 3-5.6 3.6-9.2 2.4 1.4 3.8 3.6 3.8 5.6 1-.6 1.6-1.6 1.8-2.8 1.6 1.6 2.8 3.6 2.8 6.4 0 3.4-2.4 5.8-6 5.8z"/>',
    drop: '<path d="M12 3.5s6 6.6 6 10.8a6 6 0 0 1-12 0c0-4.2 6-10.8 6-10.8z"/>',
    wind: '<path d="M3.5 9.5h11a2.5 2.5 0 1 0-2.5-2.5M3.5 14.5h15a2.5 2.5 0 1 1-2.5 2.5M3.5 12h7"/>',
    sun: SUN,
    moon: '<path class="moon" d="M19.5 14.6A7.6 7.6 0 0 1 9.4 4.5a7.6 7.6 0 1 0 10.1 10.1z"/>',
    pin: '<path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0c0 5.4-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.3"/>',
    flag: '<path d="M5 21V4.5M5 4.5h11l-2 4 2 4H5"/>',
    user: '<circle cx="12" cy="8" r="3.8"/><path d="M4.5 20a7.5 7.5 0 0 1 15 0"/>',
    mail: '<rect x="3.5" y="5.5" width="17" height="13" rx="2"/><path d="m4 7 8 6 8-6"/>',
    gift: '<rect x="3.5" y="8.5" width="17" height="4" rx="1"/><path d="M5 12.5V20h14v-7.5M12 8.5V20M12 8.5S10.5 4 8 4.5 7.5 8.5 12 8.5zm0 0s1.5-4.5 4-4 .5 4-4 4z"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    more: '<circle cx="6" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="18" cy="12" r="1.2"/>',
    refresh: '<path d="M20 6v5h-5"/><path d="M19.4 11A7.5 7.5 0 1 0 17 17.3"/>',
    edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
    trash: '<path d="M3.5 6.5h17M8.5 6.5V4.5h7v2M6 6.5l1 13.5h10l1-13.5"/>',
    close: '<path d="m6 6 12 12M18 6 6 18"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    up: '<path d="m6 14 6-6 6 6"/>',
    down: '<path d="m6 10 6 6 6-6"/>',
    flat: '<path d="M6 12h12"/>',
    layout: '<rect x="3.5" y="3.5" width="7.5" height="7.5" rx="2"/><rect x="13" y="3.5" width="7.5" height="7.5" rx="2"/><rect x="3.5" y="13" width="17" height="7.5" rx="2"/>',
    sidebar: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M9.5 4.5v15"/>',
    appbar: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M3.5 9h17M14.5 6.8h3"/>',
    sparkle: '<path d="m12 3 1.7 5.3L19 10l-5.3 1.7L12 17l-1.7-5.3L5 10l5.3-1.7z"/><path d="M19 15.5l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z"/>',
    back: '<path d="m15 18-6-6 6-6"/>',
    alert: '<path d="M12 3.5 21 19.5H3z"/><path d="M12 10v4.5M12 17.2v.3"/>',
    locate: '<circle cx="12" cy="12" r="3"/><circle cx="12" cy="12" r="7.5"/><path d="M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/>',
    grip: '<circle cx="9" cy="7" r="1"/><circle cx="15" cy="7" r="1"/><circle cx="9" cy="12" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="9" cy="17" r="1"/><circle cx="15" cy="17" r="1"/>',
    dot: '<circle cx="12" cy="12" r="3.5"/>',
    cpu: '<rect x="6" y="6" width="12" height="12" rx="2.2"/><rect x="9.5" y="9.5" width="5" height="5" rx=".8"/><path d="M9.5 3.5v2.5M14.5 3.5v2.5M9.5 18v2.5M14.5 18v2.5M3.5 9.5H6M3.5 14.5H6M18 9.5h2.5M18 14.5h2.5"/>',
    gpu: '<rect x="2.5" y="6.5" width="19" height="10" rx="2"/><circle cx="9" cy="11.5" r="2.6"/><circle cx="16" cy="11.5" r="2.6"/><path d="M5 16.5v3M2.5 9h-1"/>',
    memory: '<rect x="3" y="7" width="18" height="10" rx="1.6"/><path d="M7 10v4M11 10v4M15 10v4M6 17v2.5M10 17v2.5M14 17v2.5M18 17v2.5"/>',
    disk: '<rect x="3.5" y="4" width="17" height="16" rx="2.5"/><circle cx="12" cy="11" r="4"/><circle cx="12" cy="11" r=".8"/><path d="M7 17.5h2"/>',
    network: '<path d="M12 20.5v-5M8 15.5h8M7 20.5h10"/><path d="M4.5 9.5a10.5 10.5 0 0 1 15 0M7.3 12.3a6.5 6.5 0 0 1 9.4 0"/>',
    battery: '<rect x="2.5" y="7.5" width="17" height="9" rx="2"/><path d="M21.5 10.5v3"/><path d="M5.5 10.5v3M8.5 10.5v3"/>',
    gauge: '<path d="M4.2 17.5a8.5 8.5 0 1 1 15.6 0"/><path d="m12 13 4-4.5"/><circle cx="12" cy="13" r="1.4"/>',
    external: '<path d="M14 4.5h5.5V10M19.5 4.5 11 13M18 14v4.5a1 1 0 0 1-1 1H5.5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1H10"/>',
    info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5M12 7.8v.2"/>',
    list: '<path d="M8.5 6.5h12M8.5 12h12M8.5 17.5h12"/><circle cx="4.5" cy="6.5" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="17.5" r="1"/>',
    key: '<circle cx="8" cy="15" r="4.5"/><path d="m11.3 11.7 8.2-8.2M16.5 6.5l2.5 2.5M14 9l2 2"/>',
    folder: '<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
    claude: '<path d="M12 3v18M3 12h18M5.6 5.6l12.8 12.8M18.4 5.6 5.6 18.4" stroke-width="2.1"/>',
    codex: '<rect x="3" y="4.5" width="18" height="15" rx="3"/><path d="m7.5 10 3 2.5-3 2.5M12.5 15.5h4"/>',
    antigravity: '<path d="M12 3.5 20.5 19h-4.2L12 11l-4.3 8H3.5z"/>',
    todo: '<rect x="3.5" y="3.5" width="17" height="17" rx="4"/><path d="m8 12.2 2.8 2.8L16.5 9"/>',
    timer: '<circle cx="12" cy="13.5" r="7.5"/><path d="M12 13.5V9.5M9.5 2.5h5M18.5 6.5l1.5-1.5"/>',
    server: '<rect x="3.5" y="4" width="17" height="7" rx="2"/><rect x="3.5" y="13" width="17" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01M11 7.5h6M11 16.5h6"/>',
    github: '<path d="M9 19c-4 1.3-4-2-5.5-2.5M14.5 21v-3.4a3 3 0 0 0-.8-2.3c2.7-.3 5.5-1.3 5.5-6a4.6 4.6 0 0 0-1.3-3.2 4.3 4.3 0 0 0-.1-3.2s-1-.3-3.4 1.3a11.6 11.6 0 0 0-6 0C6 2.6 5 2.9 5 2.9a4.3 4.3 0 0 0-.1 3.2 4.6 4.6 0 0 0-1.3 3.2c0 4.6 2.8 5.7 5.5 6a3 3 0 0 0-.8 2.3V21"/>',
    note: '<path d="M5.5 3.5h9l4 4v13h-13z"/><path d="M14.5 3.5v4h4M8.5 12h7M8.5 15.5h7M8.5 8.5h3"/>',
    play: '<path d="M8 5.5v13l10.5-6.5z"/>',
    pause: '<path d="M8.5 5.5v13M15.5 5.5v13"/>',
    stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="2"/>',
    skip: '<path d="M6 5.5v13l9-6.5zM18 5.5v13"/>',
    flag2: '<path d="M6 21V4M6 4h10l-1.5 3.5L16 11H6"/>',
    filter: '<path d="M4 5.5h16l-6 7.5v5.5l-4 1.5V13z"/>',
    repeat: '<path d="M17 2.5 20 5.5l-3 3"/><path d="M4 11.5v-1a5 5 0 0 1 5-5h11M7 21.5 4 18.5l3-3"/><path d="M20 12.5v1a5 5 0 0 1-5 5H4"/>',
    tag: '<path d="M3.5 12V4.5a1 1 0 0 1 1-1H12l8.5 8.5-8.5 8.5z"/><circle cx="8" cy="8" r="1.4"/>',
    chevron: '<path d="m9 6 6 6-6 6"/>',
    drag: '<circle cx="9" cy="6.5" r="1"/><circle cx="15" cy="6.5" r="1"/><circle cx="9" cy="12" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="9" cy="17.5" r="1"/><circle cx="15" cy="17.5" r="1"/>',
    sliders: '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',

    ztop: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><rect x="6.5" y="7" width="11" height="3" rx="1"/>',
    zcomposer: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M8 9h8"/><rect x="6.5" y="12" width="11" height="4" rx="1.6"/>',
    zhome: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><rect x="6.5" y="7" width="11" height="3.2" rx="1.4"/><path d="M6.5 13.5h4.5M13 13.5h4.5M6.5 16.5h4.5M13 16.5h4.5"/>',
    zleft: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><rect x="6" y="7" width="4" height="10" rx="1"/>',
    zright: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><rect x="14" y="7" width="4" height="10" rx="1"/>',
    zside: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M9.5 4.5v15M5.5 8h2M5.5 10.5h2"/>',
    zsbottom: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M9.5 4.5v15M3.5 15h6"/>',
    zbar: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M3.5 9h17M13 6.8h4.5"/>',
    arrange: '<rect x="3.5" y="3.5" width="7" height="7" rx="1.6"/><rect x="13.5" y="3.5" width="7" height="4.5" rx="1.4"/><rect x="13.5" y="11" width="7" height="9.5" rx="1.6"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.6"/>',
    undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    redo: '<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
    resize: '<path d="M20 13v7h-7M20 20l-7-7M4 11V4h7M4 4l7 7"/>',
    reset: '<path d="M4 12a8 8 0 1 0 2.3-5.6"/><path d="M4 4v4.5h4.5"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    unpin: '<path d="m6 6 12 12M18 6 6 18"/>',
  };
  const ico = (name, cls = "") => `<span class="mp-ico ${cls}" aria-hidden="true"><svg viewBox="0 0 24 24">${ICONS[name] || ICONS.dot}</svg></span>`;
  const KIND_ICON = { weather: "partly", currency: "coin", crypto: "btc", stock: "chart", rss: "news", countdown: "calendar",
    sports: "trophy", web: "globe", custom: "sparkle", news: "news", system: "cpu", usage: "gauge",
    todo: "todo", notes: "note", clock: "clock", timers: "timer", proxy: "server", calendar: "calendar", github: "github", module: "sparkle" };

  const LOCAL_KINDS = ["todo", "notes", "clock", "timers", "module"];
  const AI_KINDS = ["custom", "sports", "web", "news"];
  const PROVIDERS = {
    claude: { name: "Claude", icon: "claude", modes: ["auto", "cli", "desktop", "dir", "token"] },
    codex: { name: "Codex", icon: "codex", modes: ["auto", "cli", "dir", "token"] },
    antigravity: { name: "Antigravity", icon: "antigravity", modes: ["auto", "dir"] },
    kiro: { name: "Kiro", icon: "gauge", modes: ["proxy"] },
    proxy: { name: "MemuAI Proxy", icon: "gauge", modes: ["proxy"] },
    api_budget: { name: t("Баланс API", "API balance"), icon: "gauge", modes: ["proxy"] },
    quota_planner: { name: t("Планировщик квот", "Quota planner"), icon: "gauge", modes: ["proxy"] },
  };
  const modeName = (provider, mode) => ({
    auto: t("Автоматически", "Automatic"),
    cli: provider === "codex" ? t("Codex CLI (~/.codex)", "Codex CLI (~/.codex)") : t("Claude Code (~/.claude)", "Claude Code (~/.claude)"),
    desktop: t("Claude Desktop (Windows)", "Claude Desktop (Windows)"),
    dir: provider === "antigravity" ? t("Своя папка ~/.gemini", "Custom ~/.gemini folder") : t("Своя папка конфигурации", "Custom config folder"),
    token: t("Вставить токен", "Paste a token"),
    proxy: t("Из MemuAI Proxy", "From MemuAI Proxy"),
  }[mode] || mode);
  const NEWS_TOPICS = () => [["ai", t("ИИ и нейросети", "AI")], ["tech", t("Технологии", "Tech")], ["science", t("Наука", "Science")],
    ["crypto", t("Крипто", "Crypto")], ["security", t("Кибербезопасность", "Security")], ["space", t("Космос", "Space")],
    ["gaming", t("Игры", "Gaming")], ["", t("Своя тема…", "Custom topic…")]];

  const TPLS = () => ({
    weather: { ru: ["Погода", "Сейчас и на 5 дней · Open-Meteo, без ключа"], en: ["Weather", "Now and 5 days · Open-Meteo, no key"],
      fields: [{ key: "city", ru: "Город", en: "City", hint_ru: "Пусто — местоположение из «Настройки → Панели»", hint_en: "Empty — the location from Settings → Panels", geo: true }] },
    currency: { ru: ["Курс валют", "ЦБ РФ для рубля, ЕЦБ для остальных"], en: ["Exchange rate", "Bank of Russia for RUB, ECB otherwise"],
      fields: [{ key: "base", ru: "Из", en: "From", def: "USD", options: ["USD", "EUR", "CNY", "GBP", "JPY", "TRY", "KZT", "BYN", "CHF", "RUB"] },
        { key: "quote", ru: "В", en: "To", def: "RUB", options: ["RUB", "USD", "EUR", "CNY", "GBP", "JPY", "KZT", "CHF"] }] },
    crypto: { ru: ["Криптовалюта", "Цена и график за сутки · CoinGecko"], en: ["Crypto", "Price and 24h chart · CoinGecko"],
      fields: [{ key: "coin", ru: "Монета", en: "Coin", def: "bitcoin", options: [["bitcoin", "Bitcoin"], ["ethereum", "Ethereum"], ["the-open-network", "Toncoin"], ["solana", "Solana"], ["tether", "Tether"], ["binancecoin", "BNB"], ["ripple", "XRP"], ["dogecoin", "Dogecoin"]] },
        { key: "vs", ru: "В валюте", en: "In", def: "usd", options: [["usd", "USD"], ["eur", "EUR"], ["rub", "RUB"]] }] },
    stock: { ru: ["Акции", "Мосбиржа или биржи США"], en: ["Stocks", "Moscow Exchange or US markets"],
      fields: [{ key: "symbol", ru: "Тикер", en: "Ticker", def: "SBER", placeholder: "SBER, GAZP, AAPL…" },
        { key: "exchange", ru: "Биржа", en: "Exchange", def: "moex", options: [["moex", t("Мосбиржа", "Moscow Exchange")], ["us", t("США (Yahoo)", "US (Yahoo)")]] }] },
    rss: { ru: ["Новости (RSS)", "Заголовки любой RSS- или Atom-ленты"], en: ["Headlines (RSS)", "Any RSS or Atom feed"],
      fields: [{ key: "url", ru: "Адрес ленты", en: "Feed URL", placeholder: "https://…/rss", required: true },
        { key: "title", ru: "Название", en: "Title", placeholder: t("Необязательно", "Optional") }] },
    countdown: { ru: ["Обратный отсчёт", "Дни, часы и минуты до события"], en: ["Countdown", "Days, hours and minutes to an event"],
      fields: [{ key: "title", ru: "Событие", en: "Event", placeholder: t("Отпуск, релиз, день рождения…", "Vacation, release, birthday…"), required: true },
        { key: "target", ru: "Когда", en: "When", type: "datetime-local", required: true }] },
    sports: { ru: ["Спорт", "Счёт и ближайший матч — ищет ИИ"], en: ["Sports", "Score and next game — found by AI"],
      fields: [{ key: "team", ru: "Команда или турнир", en: "Team or league", placeholder: t("Зенит, РПЛ, Real Madrid…", "Real Madrid, NBA…"), required: true }] },
    web: { ru: ["Значение со страницы", "ИИ следит за числом на сайте"], en: ["Value from a page", "AI watches a figure on a site"],
      fields: [{ key: "url", ru: "Адрес страницы", en: "Page URL", placeholder: "https://…", required: true },
        { key: "what", ru: "Что показывать", en: "What to show", placeholder: t("Цена товара, число подписчиков…", "Product price, follower count…"), required: true }] },
    custom: { ru: ["Своя панель с ИИ", "Опишите словами — ИИ соберёт и будет обновлять"], en: ["Custom AI panel", "Describe it — AI builds and refreshes it"],
      fields: [{ key: "brief", ru: "Что показывать", en: "What to show", type: "textarea", required: true,
        placeholder: t("Например: курс золота и его изменение за день", "E.g. the gold price and its daily change") }] },
    news: { ru: ["Новости по теме", "Первоисточники, без повторов, с проверкой значимости"], en: ["Topic news", "Primary sources, no repeats, ranked by significance"],
      fields: [{ key: "topic", ru: "Тема", en: "Topic", def: "ai", options: NEWS_TOPICS() },
        { key: "brief", ru: "Уточнение или своя тема", en: "Refine or your own topic", type: "textarea",
          placeholder: t("Например: релизы моделей и open-source, без корпоративных кейсов", "E.g. model releases and open source, no customer stories") },
        { key: "count", ru: "Сколько новостей", en: "How many", def: "5", options: ["3", "4", "5", "6", "8", "10"] }] },
    system: { ru: ["Производительность ПК", "CPU, GPU, память, диски, сеть, температура, батарея"], en: ["PC performance", "CPU, GPU, RAM, disks, network, temperature, battery"],
      fields: [] },
    usage: { ru: ["Лимиты ИИ", "Claude, Codex, Antigravity, Kiro и API-балансы"], en: ["AI limits", "Claude, Codex, Antigravity, Kiro and API balances"],
      fields: [] },
    todo: { ru: ["Задачи (To-Do)", "Списки дел: сроки, приоритеты, теги, подзадачи, повторы, напоминания"], en: ["To-Do", "Task lists: due dates, priorities, tags, subtasks, repeats, reminders"],
      fields: [] },
    notes: { ru: ["Заметка", "Текст или чек-лист под рукой, сохраняется сам"], en: ["Note", "Text or a checklist at hand, saved automatically"], fields: [] },
    clock: { ru: ["Часы", "Местное время и другие часовые пояса"], en: ["Clock", "Local time and other time zones"], fields: [] },
    timers: { ru: ["Помодоро и таймеры", "Таймеры, секундомер и помодоро «Быстрого доступа»"], en: ["Pomodoro and timers", "Quick Ask timers, stopwatch and Pomodoro"], fields: [] },
    proxy: { ru: ["MemuAI Proxy", "Запросы, токены, задержка и стоимость"], en: ["MemuAI Proxy", "Requests, tokens, latency and cost"], fields: [] },
    calendar: { ru: ["Календарь", "Ближайшие события Google Calendar"], en: ["Calendar", "Upcoming Google Calendar events"], fields: [] },
    github: { ru: ["GitHub", "Уведомления, ревью и назначенное вам"], en: ["GitHub", "Notifications, reviews and what is assigned to you"], fields: [] },
  });

  const GALLERY_GROUPS = () => [
    [t("Главное", "Essentials"), ["weather", "news", "todo", "system"]],
    [t("Время и дела", "Time and tasks"), ["clock", "timers", "countdown", "notes", "calendar"]],
    [t("Финансы", "Money"), ["currency", "crypto", "stock"]],
    [t("ИИ и разработка", "AI and dev"), ["usage", "proxy", "github"]],
    [t("Ленты и свои панели с ИИ", "Feeds and custom AI panels"), ["rss", "sports", "web", "custom"]],
  ];
  const GALLERY_ORDER = GALLERY_GROUPS().flatMap((g) => g[1]);
  const tplName = (kind) => { const T = TPLS(); return T[kind] ? T[kind][en() ? "en" : "ru"][0] : kind; };
  const tplDesc = (kind) => { const T = TPLS(); return T[kind] ? T[kind][en() ? "en" : "ru"][1] : ""; };
  const placeName = (place) => ({ home: t("Под полем ввода", "Below the message box"), sidebar: t("Боковая панель", "Sidebar"), appbar: t("Верхняя панель", "Top bar"),
    top: t("Над приветствием", "Above the greeting"), composer: t("Над полем ввода", "Above the message box"), left: t("Колонка слева", "Left column"),
    right: t("Колонка справа", "Right column"), sidebar_bottom: t("Низ боковой панели", "Sidebar bottom") }[place] || place);
  const placeIcon = (place) => ({ home: "zhome", sidebar: "zside", appbar: "zbar", top: "ztop", composer: "zcomposer", left: "zleft", right: "zright", sidebar_bottom: "zsbottom" }[place]);

  const BIND = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*)((?:\.[A-Za-z0-9_]+)*)\s*\}\}/g;
  const WHOLE = /^\{\{\s*([A-Za-z_][A-Za-z0-9_]*)((?:\.[A-Za-z0-9_]+)*)\s*\}\}$/;
  function lookup(root, rest, data) {
    let value = data ? data[root] : undefined;
    for (const part of String(rest || "").split(".").filter(Boolean)) {
      if (value == null) return undefined;
      value = value[/^\d+$/.test(part) ? +part : part];
    }
    return value;
  }
  function show(value) {
    if (value == null) return "";
    if (typeof value === "number") return Number.isFinite(value) ? value.toLocaleString(en() ? "en-US" : "ru-RU", { maximumFractionDigits: Math.abs(value) < 1 ? 6 : 2 }) : "";
    if (typeof value === "boolean") return value ? t("да", "yes") : t("нет", "no");
    if (typeof value === "object") return "";
    return isoShown(String(value));
  }

  const ISO_VALUE = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$/;
  function isoShown(value) {
    const m = ISO_VALUE.exec(value.trim());
    if (!m) return value;
    const loc = en() ? "en-GB" : "ru-RU";
    const year = +m[1] !== new Date().getFullYear() ? { year: "numeric" } : {};
    try {
      if (!m[4]) {
        const d = new Date(+m[1], +m[2] - 1, +m[3]);
        return d.getMonth() === +m[2] - 1 ? d.toLocaleDateString(loc, { day: "numeric", month: "short", ...year }) : value;
      }
      const d = m[6] ? new Date(value.trim().replace(" ", "T")) : new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
      if (Number.isNaN(+d)) return value;
      return d.toLocaleString(loc, { day: "numeric", month: "short", ...year, hour: "2-digit", minute: "2-digit" });
    } catch (e) { return value; }
  }

  const plain = (field, data) => { const v = raw(field, data); return v == null || typeof v === "object" ? "" : String(v); };

  function raw(field, data) {
    if (typeof field !== "string") return field;
    const whole = field.trim().match(WHOLE);
    if (whole) return lookup(whole[1], whole[2], data);
    return field.replace(BIND, (_, root, rest) => show(lookup(root, rest, data)));
  }
  const text = (field, data) => show(raw(field, data));

  const trendOf = (b, data) => {
    const delta = text(b.delta, data);
    const sign = /\{\{/.test(String(b.delta || "")) && !/\{\{/.test(String(b.trend || "")) ? /^\s*([+\-−–])\s*\d/.exec(delta) : null;
    if (sign) return sign[1] === "+" ? "up" : "down";
    return text(b.trend, data);
  };

  function ago(ts) {
    if (!ts) return "";
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 60) return t("только что", "just now");
    if (s < 3600) return t(`${Math.floor(s / 60)} мин назад`, `${Math.floor(s / 60)} min ago`);
    if (s < 86400) return t(`${Math.floor(s / 3600)} ч назад`, `${Math.floor(s / 3600)} h ago`);
    return new Date(ts * 1000).toLocaleDateString(en() ? "en-GB" : "ru-RU", { day: "numeric", month: "short" });
  }
  const DAY_SHORT = () => (en() ? ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"] : ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]);
  function daysLabel(days) {
    if (!Array.isArray(days) || !days.length || days.length === 7) return "";
    const key = days.join(",");
    if (key === "0,1,2,3,4") return t("по будням", "on weekdays");
    if (key === "5,6") return t("по выходным", "on weekends");
    return t("по ", "on ") + days.map((d) => DAY_SHORT()[d]).join(", ");
  }
  function cadenceLabel(c) {
    if (!c || c.kind === "manual") return t("вручную", "manual");
    if (c.kind === "daily") return t(`ежедневно в ${c.at}`, `daily at ${c.at}`);
    if (c.kind === "times") return [t("в ", "at ") + (c.at || []).join(", "), daysLabel(c.days)].filter(Boolean).join(" ");
    const m = +c.minutes || 60;
    let base;
    if (m % 1440 === 0) base = m === 1440 ? t("раз в день", "once a day") : t(`раз в ${m / 1440} дн.`, `every ${m / 1440} d`);
    else if (m % 60 === 0) base = m === 60 ? t("каждый час", "hourly") : t(`каждые ${m / 60} ч`, `every ${m / 60} h`);
    else if (m === 1) base = t("каждую минуту", "every minute");
    else base = t(`каждые ${m} мин`, `every ${m} min`);
    const hours = c.from && c.to ? t(` с ${c.from} до ${c.to}`, ` ${c.from}–${c.to}`) : "";
    return [base + hours, daysLabel(c.days)].filter(Boolean).join(" ");
  }

  function untilText(ts) {
    const left = Math.max(0, Math.round(+ts - Date.now() / 1000));
    if (!Number.isFinite(left) || !ts) return "";
    if (left < 60) return t("меньше минуты", "under a minute");
    const d = Math.floor(left / 86400), h = Math.floor((left % 86400) / 3600), m = Math.floor((left % 3600) / 60);
    if (d) return t(`${d} дн ${h} ч`, `${d}d ${h}h`);
    if (h) return t(`${h} ч ${m} мин`, `${h}h ${m}m`);
    return t(`${m} мин`, `${m} min`);
  }
  function clockText(ts) {
    if (!ts) return "";
    const d = new Date(+ts * 1000);
    const sameDay = d.toDateString() === new Date().toDateString();
    return d.toLocaleString(en() ? "en-GB" : "ru-RU", sameDay ? { hour: "2-digit", minute: "2-digit" } : { weekday: "short", hour: "2-digit", minute: "2-digit" });
  }
  const STAGES = ["plan", "collect", "dedupe", "score", "summarize"];
  function stageText(progress) {
    if (!progress) return "";
    const d = progress.detail || "";
    return ({
      plan: t("Планирую источники", "Planning sources"),
      collect: d ? t(`Читаю ${d} источников`, `Reading ${d} sources`) : t("Читаю источники", "Reading sources"),
      dedupe: d ? t(`Склеиваю повторы · ${d} кандидатов`, `Merging duplicates · ${d} candidates`) : t("Склеиваю повторы", "Merging duplicates"),
      score: t("Оцениваю значимость и достоверность", "Ranking significance and credibility"),
      summarize: t("Пишу короткую сводку", "Writing a short digest"),
      fetch: d ? t(`Читаю ${d}`, `Reading ${d}`) : t("Читаю страницу", "Reading the page"),
      search: t("Ищу в интернете", "Searching the web"),
      model: t("Модель сверяет значения с источниками", "The model checks values against sources"),
      compose: t("Модель собирает панель", "The model designs the panel"),
    })[progress.stage] || t("Обновляю…", "Refreshing…");
  }

  function wallText(wall, panel = null) {
    if (!wall) return "";
    const [kind, reason = ""] = String(wall.status || "").split(":");

    const later = panel?.cadence?.kind === "manual"
      ? t(" — нажмите «Повторить».", " — press Retry.") : t(" — попробую позже.", " — will retry later.");
    const map = {
      needs_login: t(`Источник просит вход (${reason}). Откройте настройки панели и укажите другой источник.`, `The source asks for a sign-in (${reason}). Edit the panel to use another source.`),
      needs_connector: t("Нужно подключение к сервису.", "A service connection is needed."),
      paywall: t(`Источник за платным доступом (${reason}).`, `The source is paywalled (${reason}).`),
      source_gone: t("Источник больше не отвечает или не нашёл данные.", "The source is gone or has no such data."),
      needs_location: t("Где вы? Укажите город или разрешите определить местоположение.", "Where are you? Set a city or allow location lookup."),
    };
    const blocked = {
      "rate-limit": t("Источник временно ограничил запросы", "The source is rate-limiting") + later,
      "proxy-offline": t("MemuAI Proxy не запущен — обновление по модели недоступно.", "MemuAI Proxy is not running — AI refresh is unavailable."),
      "no-model": t("Не выбрана модель для обновления (Настройки → Панели).", "No model for refreshing (Settings → Panels)."),
      "not-found": t("В источниках не нашлось нужных значений.", "The sources did not contain the values."),
      "source-down": t("Источник не ответил", "The source did not answer") + later,
      "model-error": t("Модель вернула ошибку.", "The model returned an error."),
      "model-timeout": t("Модель не успела ответить", "The model timed out") + later,

      "model-rate-limit": t("Модель ограничила запросы (лимит провайдера)", "The model is rate-limited by its provider") + later,
      "model-missing": t("Модель панели не найдена — выберите другую в настройках панели.", "The panel's model was not found — pick another one in the panel settings."),
      "model-auth": t("Провайдер модели отклонил ключ API — проверьте аккаунт в MemuAI Proxy.", "The model's provider rejected the API key — check the account in MemuAI Proxy."),
      "model-down": t("Модель сейчас недоступна", "The model is unavailable right now") + later,
      "model-format": t("Модель ответила не в нужном формате — попробуйте другую модель.", "The model answered in the wrong format — try another model."),
      "bad-spec": t("Модель не смогла собрать панель — попробуйте другую модель или опишите иначе.", "The model could not design the panel — try another model or rephrase."),
      "no-web": t("Веб-поиск недоступен.", "Web search is unavailable."),
      "forbidden": t("Сайт не пустил (защита от ботов)", "The site refused (bot protection)") + later,
    };
    if (kind === "needs_connector" && reason === "usage") return t("Добавьте локальный аккаунт или выберите аккаунт MemuAI Proxy.", "Add a local account or select a MemuAI Proxy account.");
    if (kind === "blocked" && reason.startsWith("forbidden")) return blocked.forbidden;
    let base = kind === "blocked" ? (blocked[reason] || "") : (map[kind] || "");
    let detail = String(wall.message || "").trim();

    if (detail && en() && /[А-Яа-яЁё]/.test(detail)) detail = "";
    if (!base) base = detail || t("Не удалось обновить.", "Could not refresh.");

    else if (detail && (kind === "source_gone" || kind === "needs_connector") && (en() || /[А-Яа-яЁё]/.test(detail))) base = detail;

    else if (detail && kind === "blocked" && /^model-(error|rate-limit|down|missing|auth)$/.test(reason)
      && (en() ? !/[А-Яа-яЁё]/.test(detail) : /[А-Яа-яЁё]/.test(detail))) base = detail;
    return base;
  }

  function wallButton(wall) {
    const [kind, reason = ""] = String(wall?.status || "").split(":");
    if (kind === "needs_connector" && reason && reason !== "usage") return `<button type="button" data-mp-act="connect" data-mp-connector="${esc(reason)}">${t("Подключить", "Connect")}</button>`;
    return `<button type="button" data-mp-act="refresh">${t("Повторить", "Retry")}</button>`;
  }
  async function openIntegrations(service = "") {
    const sub = /^[a-z_]{2,40}$/.test(service) ? "int-" + service : "";
    if (EMBED) {
      fetch("/api/quick/window", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "handoff", handoff: { settings: "apps" } }) }).catch(() => {});
      return;
    }
    if (typeof showSettings === "function") await showSettings(true);
    if (typeof openSettingsPage === "function") openSettingsPage("apps", sub ? { sub } : {});
  }

  const SIZE_RANK = { s: 0, m: 1, l: 2 };
  const DEFAULT_MIN = { stats: "m", strip: "m", image: "m", link: "m", action: "m" };

  function effectiveSize(panel, mode) {
    if (mode === "home") return panel.size === "w" ? "l" : (panel.size || "m");
    if (mode === "sidebar") return "s";
    return "l";
  }

  const spanOf = (panel) => ({ s: 1, m: 2, l: 2, w: 4 }[panel.size] || 2);
  const SIZE_NAMES = () => ({ s: t("Малая", "Small"), m: t("Средняя", "Medium"), l: t("Большая", "Large"), w: t("Широкая", "Wide") });
  const SIZE_HINTS = () => ({ s: t("¼ ширины, главное значение", "¼ width, the key value"), m: t("½ ширины", "½ width"),
    l: t("½ ширины, всё содержимое", "½ width, all details"), w: t("Вся ширина, всё содержимое", "Full width, all details") });
  let sparkSeq = 0;
  function sparkline(points, label, unit) {
    const nums = (Array.isArray(points) ? points : []).map(Number).filter(Number.isFinite);
    if (nums.length < 2) return "";
    const min = Math.min(...nums), max = Math.max(...nums), span = max - min || 1;
    const xy = nums.map((v, i) => [(i / (nums.length - 1)) * 100, 36 - ((v - min) / span) * 32 + 2]);
    const line = xy.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(2)} ${y.toFixed(2)}`).join(" ");
    const id = `mp-sg-${++sparkSeq}`;
    const last = xy[xy.length - 1];
    return `<div class="mp-spark" role="img" aria-label="${esc(label || t("График", "Chart"))}">
      <svg viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true">
        <defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="currentColor" stop-opacity=".22"/><stop offset="1" stop-color="currentColor" stop-opacity="0"/></linearGradient></defs>
        <path class="area" style="fill:url(#${id});color:var(--mp-spark,var(--accent))" d="${line} L100 40 L0 40 Z"/>
        <path class="line" d="${line}"/>
      </svg>
      <i class="mp-dot" style="left:${last[0].toFixed(2)}%;top:${(last[1] / 40 * 100).toFixed(2)}%"></i>
    </div>${label ? `<div class="mp-spark-label">${esc(label)}${unit ? " · " + esc(unit) : ""}</div>` : ""}`;
  }
  const plural = (n, one, few, many) => { const a = Math.abs(n) % 100, b = a % 10; return a > 10 && a < 20 ? many : b === 1 ? one : b >= 2 && b <= 4 ? few : many; };
  const daysWord = (n) => (en() ? (n === 1 ? "day" : "days") : plural(n, "день", "дня", "дней"));
  function countdownCells(target, size, opts = {}) {
    const end = Date.parse(target || "");
    if (!Number.isFinite(end)) return "";
    let left = Math.floor((end - Date.now()) / 1000);
    let past = false;
    if (left <= 0) {
      if (opts.after !== "up") return `<div class="mp-cd-done">${t("Событие наступило", "It's here")}</div>`;
      past = true; left = -left;
    }
    const total = left;
    const d = Math.floor(left / 86400); left %= 86400;
    const h = Math.floor(left / 3600); left %= 3600;
    const m = Math.floor(left / 60); const s = left % 60;
    const pad = (v) => String(v).padStart(2, "0");
    const units = opts.units || "auto";
    const style = opts.style || "cells";
    const prefix = past ? `<small class="mp-cd-past">${t("прошло", "since")}</small>` : "";
    if (style === "big") {
      const main = d >= 1 && units !== "dhms" ? `<b>${d}</b><span>${esc(daysWord(d))}</span>${units === "days" ? "" : `<em>${pad(h)}:${pad(m)}</em>`}`
        : `<b>${pad(h)}:${pad(m)}${units === "dhm" ? "" : `<small>:${pad(s)}</small>`}</b>`;
      return `<div class="mp-cd-big">${prefix}${main}</div>`;
    }
    if (style === "progress") {
      const start = Date.parse(opts.since || "");
      const span = Number.isFinite(start) && end > start ? end - start : 0;
      const pct = span ? Math.max(0, Math.min(100, ((Date.now() - start) / span) * 100)) : 0;
      const label = d ? (size === "s" ? `${d} ${daysWord(d)}` : t(`${d} ${daysWord(d)} ${h} ч`, `${d} ${daysWord(d)} ${h} h`)) : t(`${h} ч ${m} мин`, `${h} h ${m} min`);
      return `<div class="mp-cd-prog">${prefix}<div class="mp-progress-top"><b>${esc(past ? label : t("Осталось ", "Left ") + label)}</b><span>${past ? "100" : Math.round(pct)}%</span></div>
        <div class="mp-bar" role="progressbar" aria-valuenow="${Math.round(pct)}" aria-valuemin="0" aria-valuemax="100"><i style="width:${past ? 100 : pct.toFixed(2)}%"></i></div></div>`;
    }
    let cells;
    if (units === "days") cells = [[d, en() ? "days" : plural(d, "день", "дня", "дней")]];
    else {
      cells = [[d, t("дн", "d")], [h, t("ч", "h")], [m, t("мин", "min")]];
      if (units === "dhms" || (units === "auto" && size !== "s")) cells.push([s, t("сек", "s")]);
    }
    void total;
    return prefix + cells.map(([v, l], i) => `<div class="mp-cd-cell"><b>${i === 0 ? String(v) : pad(v)}</b><small>${l}</small></div>`).join("");
  }
  function block(b, data, size, ctx) {
    const min = b.min_size || DEFAULT_MIN[b.type] || "s";
    if (SIZE_RANK[size] < SIZE_RANK[min]) return "";
    if (b.max_size && SIZE_RANK[size] > SIZE_RANK[b.max_size]) return "";
    const iconName = b.icon ? text(b.icon, data) : "";
    switch (b.type) {
      case "title": {
        const main = text(b.text, data) || ctx.title;
        const sub = text(b.subtitle, data);
        return `<div class="mp-title">${iconName ? ico(iconName) : ""}<div class="mp-title-text"><b title="${esc(main)}">${esc(main)}</b>${sub ? `<small title="${esc(sub)}">${esc(sub)}</small>` : ""}</div></div>`;
      }
      case "metric": {
        const value = text(b.value, data);
        const trend = trendOf(b, data);
        const delta = text(b.delta, data);
        if (trend && !ctx.trend) ctx.trend = trend;
        return `<div class="mp-metric">${iconName ? ico(iconName) : ""}<div class="mp-metric-main">
          ${b.label ? `<span class="mp-metric-label">${esc(text(b.label, data))}</span>` : ""}
          <div class="mp-metric-value"><b class="${ctx.pulse ? "mp-value-pulse" : ""}">${esc(value || "—")}</b>${b.unit ? `<span>${esc(text(b.unit, data))}</span>` : ""}
          ${delta ? `<em class="mp-delta" data-trend="${esc(trend)}">${trend === "up" || trend === "down" ? ico(trend) : ""}${esc(delta)}</em>` : ""}</div>
          ${b.caption && size !== "s" ? `<span class="mp-caption">${esc(text(b.caption, data))}</span>` : ""}</div></div>`;
      }
      case "stats": {
        const items = raw(b.items, data);
        if (!Array.isArray(items) || !items.length) return "";
        const shown = items.filter((it) => !(it.optional && !text(it.value, data))).slice(0, 8);

        const widest = Math.max(0, ...shown.map((it) => (text(it.value, data) || "—").length + (it.unit ? text(it.unit, data).length + 1 : 0)));
        return `<div class="mp-stats" style="--mp-stat-ch:${Math.min(widest, 16)}">${shown.map((it) => `<div class="mp-stat"><small>${esc(text(it.label, data))}</small><b>${esc(text(it.value, data) || "—")}${it.unit ? `<span>${esc(text(it.unit, data))}</span>` : ""}</b></div>`).join("")}</div>`;
      }
      case "strip": {
        const items = raw(b.items, data);
        if (!Array.isArray(items) || !items.length) return "";
        const cap = Math.min(b.max || 24, size === "l" ? (ctx.wide ? 12 : 8) : size === "m" ? 6 : 4);
        const shown = items.slice(0, cap);
        return `${b.label ? `<div class="mp-list-label">${esc(text(b.label, data))}</div>` : ""}<div class="mp-strip" style="--mp-cells:${shown.length}">${shown.map((it) => `<div class="mp-strip-cell"><small>${esc(text(it.label, data))}</small>${it.icon ? ico(text(it.icon, data)) : ""}<b>${esc(text(it.value, data))}</b>${it.sub ? `<em>${esc(text(it.sub, data))}</em>` : ""}</div>`).join("")}</div>`;
      }
      case "list": return listHtml(b, data, size, ctx);
      case "progress": {
        const value = Number(String(raw(b.value, data)).replace(",", "."));
        const max = Number(String(raw(b.max, data) ?? 1).replace(",", ".")) || (value > 1 ? 100 : 1);
        const pct = Number.isFinite(value) ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
        return `<div class="mp-progress"><div class="mp-progress-top"><b>${esc(text(b.label, data))}</b><span>${Math.round(pct)}%</span></div>
          <div class="mp-bar" role="progressbar" aria-valuenow="${Math.round(pct)}" aria-valuemin="0" aria-valuemax="100"><i style="width:${pct.toFixed(1)}%"></i></div>
          ${b.caption ? `<span class="mp-caption">${esc(text(b.caption, data))}</span>` : ""}</div>`;
      }
      case "sparkline": {
        const value = b.value ? text(b.value, data) : "";
        const chart = sparkline(raw(b.points, data), value ? "" : text(b.label, data), value ? "" : text(b.unit, data));
        if (!value) return chart;
        return `<div class="mp-chart"><div class="mp-chart-top"><small>${esc(text(b.label, data))}</small><b>${esc(value)}${b.unit && value !== "—" ? `<span>${esc(text(b.unit, data))}</span>` : ""}</b></div>${chart || `<div class="mp-chart-wait">${t("Собираю точки графика…", "Collecting chart points…")}</div>`}</div>`;
      }
      case "image": {
        const src = text(b.src, data);
        if (!/^(https?:|data:image\/)/i.test(src)) return "";
        return `<figure class="mp-image"><img src="${esc(src)}" alt="${esc(text(b.alt, data))}" loading="lazy" referrerpolicy="no-referrer" draggable="false">${b.caption ? `<figcaption>${esc(text(b.caption, data))}</figcaption>` : ""}</figure>`;
      }
      case "badge": return `<div class="mp-badges"><span class="mp-badge" data-tone="${esc(text(b.tone, data) || "neutral")}">${esc(text(b.text, data))}</span></div>`;
      case "text": {
        const value = text(b.text, data);
        return value ? `<p class="mp-text ${b.muted ? "muted" : ""}">${esc(value)}</p>` : "";
      }
      case "link": {
        const url = text(b.url, data);
        if (!/^https?:\/\//i.test(url)) return "";
        return `<a class="mp-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer" draggable="false">${ico("globe")}<span>${esc(text(b.text, data) || url)}</span></a>`;
      }
      case "action": return `<button type="button" class="mp-action" data-mp-ask="${esc(text(b.prompt, data))}">${ico("sparkle")}<span>${esc(text(b.text, data))}</span></button>`;
      case "countdown": {
        const opts = { style: b.style || "cells", units: b.units || "auto", after: b.after || "done", since: plain(b.since, data) || (ctx.created ? new Date(ctx.created * 1000).toISOString() : "") };
        return `<div class="mp-countdown" data-style="${esc(opts.style)}" data-mp-countdown="${esc(plain(b.target, data))}" data-mp-cd='${esc(JSON.stringify(opts))}' data-size="${size}">${countdownCells(plain(b.target, data), size, opts)}</div>`;
      }
      case "row": {
        const kids = (b.blocks || []).map((child) => block(child, data, size, ctx)).filter(Boolean);
        return kids.length ? `<div class="mp-rowbox" style="--mp-cols:${kids.length}">${kids.map((k) => `<div>${k}</div>`).join("")}</div>` : "";
      }
      case "gauge": return gaugeHtml(b, data, size);
      case "usage": return usageHtml(raw(b.accounts, data), size, ctx, b);
      case "bars": return barsHtml(b, data, size, ctx);
      case "meters": return metersHtml(b, data, size, ctx);
      case "clock": return clockHtml(b, size, ctx);
      case "todo": return todoHtml(b, size, ctx);
      case "note": return noteHtml(b, size, ctx);
      case "timers": return timersHtml(b, size, ctx);

      case "module": return window.MemuModInt ? MemuModInt.slotHtml(b, size, ctx) : "";
      default: return "";
    }
  }

  function barsHtml(b, data, size, ctx) {
    const items = raw(b.items, data);
    if (!Array.isArray(items) || !items.length) return "";
    const rows = items.map((it) => (typeof it === "object" && it ? it : { value: it }));
    const tone = (v) => (v >= 90 ? "danger" : v >= 70 ? "warn" : "ok");
    return `<div class="mp-bars-wrap">${b.label ? `<div class="mp-chart-top"><small>${esc(text(b.label, data))}</small>${b.caption ? `<b class="muted">${esc(text(b.caption, data))}</b>` : ""}</div>` : ""}
      <div class="mp-bars" role="img" aria-label="${esc(text(b.label, data))}" style="--mp-n:${rows.length}">${rows.map((r) => {
        const v = Math.max(0, Math.min(100, Number(r.value) || 0));
        return `<i data-tone="${tone(v)}" title="${esc((r.label ? r.label + ": " : "") + Math.round(v) + "%")}"><b style="height:${v.toFixed(1)}%"></b></i>`;
      }).join("")}</div></div>`;
  }
  function metersHtml(b, data, size, ctx) {
    let items = raw(b.items, data);
    if (!Array.isArray(items)) items = [];
    items = filterRows(items, ctx.filter, (r) => r.label);
    if (!items.length) return "";
    const cap = size === "s" ? 2 : size === "m" ? 4 : 8;
    return `<div class="mp-meters">${b.label ? `<div class="mp-list-label">${esc(text(b.label, data))}</div>` : ""}${items.slice(0, cap).map((r) => {
      const pct = Number(r.pct);
      const known = Number.isFinite(pct);
      return `<div class="mp-meter" data-tone="${!known ? "none" : pct >= 90 ? "danger" : pct >= 75 ? "warn" : "ok"}"><div class="mp-progress-top"><b title="${esc(r.label)}">${esc(r.label)}</b><span>${known ? Math.round(pct) + "%" : "—"}</span></div>
        <div class="mp-bar"><i style="width:${known ? Math.max(0, Math.min(100, pct)).toFixed(1) : 0}%"></i></div>${r.caption && size !== "s" ? `<span class="mp-caption">${esc(r.caption)}</span>` : ""}</div>`;
    }).join("")}</div>`;
  }

  function filterRows(rows, filter, name) {
    const words = String(filter || "").split(/[,;]+/).map((w) => w.trim().toLowerCase().replace(/[\\/:]+$/, "")).filter(Boolean);
    if (!words.length) return rows;
    return rows.filter((r) => { const n = String(name(r) || "").toLowerCase(); return words.some((w) => n.includes(w)); });
  }

  function zoneParts(zone) {
    const [tz, label] = String(zone || "").split("|");
    return { tz: tz.trim(), label: (label || "").trim() };
  }
  function zoneOffsetMin(tz, date = new Date()) {
    try {
      const parts = new Intl.DateTimeFormat("en-US", { timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
        .formatToParts(date).reduce((o, p) => { o[p.type] = p.value; return o; }, {});
      const asUtc = Date.UTC(+parts.year, +parts.month - 1, +parts.day, +parts.hour % 24, +parts.minute);
      return Math.round((asUtc - Math.floor(date.getTime() / 60000) * 60000) / 60000);
    } catch (e) { return null; }
  }
  function timeIn(tz, b) {
    const now = new Date();
    const opt = { hour: "2-digit", minute: "2-digit" };
    if (b.seconds) opt.second = "2-digit";
    if (b.h24 === "24") opt.hourCycle = "h23"; else if (b.h24 === "12") opt.hour12 = true;
    if (tz) opt.timeZone = tz;
    try { return now.toLocaleTimeString(en() ? "en-GB" : "ru-RU", opt); } catch (e) { return "—"; }
  }
  function dateIn(tz) {
    try { return new Date().toLocaleDateString(en() ? "en-GB" : "ru-RU", { weekday: "short", day: "numeric", month: "short", ...(tz ? { timeZone: tz } : {}) }); } catch (e) { return ""; }
  }
  function analogFace(tz, b, big) {
    const now = new Date();
    let h = now.getHours(), m = now.getMinutes(), s = now.getSeconds();
    if (tz) {
      const off = zoneOffsetMin(tz, now);
      if (off != null) { const local = -now.getTimezoneOffset(); const d = new Date(now.getTime() + (off - local) * 60000); h = d.getHours(); m = d.getMinutes(); s = d.getSeconds(); }
    }
    const ha = ((h % 12) + m / 60) * 30, ma = (m + s / 60) * 6, sa = s * 6;
    const ticks = Array.from({ length: 12 }, (_, i) => `<line x1="20" y1="${i % 3 ? 3.4 : 2.6}" x2="20" y2="${i % 3 ? 4.6 : 5.6}" transform="rotate(${i * 30} 20 20)"/>`).join("");
    return `<svg class="mp-analog${big ? " big" : ""}" viewBox="0 0 40 40" aria-hidden="true"><circle class="face" cx="20" cy="20" r="18.5"/><g class="ticks">${ticks}</g>
      <line class="hh" x1="20" y1="20" x2="20" y2="10.5" transform="rotate(${ha.toFixed(1)} 20 20)"/><line class="mm" x1="20" y1="20" x2="20" y2="6.5" transform="rotate(${ma.toFixed(1)} 20 20)"/>
      ${b.seconds ? `<line class="ss" x1="20" y1="22.5" x2="20" y2="5.5" transform="rotate(${sa} 20 20)"/>` : ""}<circle class="pin" cx="20" cy="20" r="1.2"/></svg>`;
  }
  function clockInner(b, size, mode) {
    const zones = (b.zones || []).map(zoneParts).filter((z) => z.tz);
    const localOff = -new Date().getTimezoneOffset();
    const diff = (tz) => {
      const off = zoneOffsetMin(tz);
      if (off == null || !b.diff) return "";
      const d = off - localOff;
      if (!d) return t("как здесь", "same");
      const hrs = Math.abs(d) / 60;
      return (d > 0 ? "+" : "−") + (Number.isInteger(hrs) ? hrs : hrs.toFixed(1)) + t(" ч", " h");
    };
    const city = (z) => z.label || z.tz.split("/").pop().replace(/_/g, " ");
    const analog = b.style === "analog";
    const head = b.local !== false || !zones.length
      ? `<div class="mp-clock-main">${analog ? analogFace("", b, true) : ""}<div><b class="mp-clock-time">${esc(timeIn("", b))}</b>${b.date !== false ? `<small>${esc(dateIn(""))}</small>` : ""}</div></div>` : "";
    const cap = size === "s" || mode === "sidebar" ? 2 : 8;
    const rows = zones.slice(0, cap).map((z) => `<div class="mp-clock-row">${analog ? analogFace(z.tz, b, false) : ""}<span class="mp-clock-city" title="${esc(z.tz)}">${esc(city(z))}<small>${esc([b.date !== false && size !== "s" ? dateIn(z.tz) : "", diff(z.tz)].filter(Boolean).join(" · "))}</small></span><b>${esc(timeIn(z.tz, b))}</b></div>`).join("");
    return head + (rows ? `<div class="mp-clock-zones">${rows}</div>` : "");
  }
  function clockHtml(b, size, ctx) {
    return `<div class="mp-clock" data-mp-clock='${esc(JSON.stringify(b))}' data-size="${size}" data-mode="${esc(ctx.mode)}">${clockInner(b, size, ctx.mode)}</div>`;
  }

  function noteRender(textValue, checklist) {
    const lines = String(textValue || "").split("\n");
    return lines.map((line, i) => {
      const m = checklist && line.match(/^\s*[-*]?\s*\[( |x|х)\]\s?(.*)$/i);
      if (m) return `<label class="mp-note-check"><input type="checkbox" data-note-line="${i}"${m[1].trim() ? " checked" : ""}><span>${esc(m[2])}</span></label>`;
      if (/^#{1,3}\s/.test(line)) return `<b class="mp-note-h">${esc(line.replace(/^#{1,3}\s/, ""))}</b>`;
      return line.trim() ? `<p>${esc(line)}</p>` : `<p class="gap"></p>`;
    }).join("");
  }
  function noteHtml(b, size, ctx) {
    const note = ctx.panel?.note || { text: "" };
    const empty = !String(note.text || "").trim();
    if (ctx.panel && state.noteEdit[ctx.panel.id] && ctx.mode !== "inline") {
      return `<div class="mp-note" data-font="${esc(b.font || "text")}" data-tsize="${esc(b.text_size || "m")}"><textarea class="mp-note-input" data-note-input data-mp-keep="note:${esc(ctx.panel.id)}" maxlength="20000" spellcheck="true" aria-label="${esc(t("Заметка", "Note"))}">${esc(note.text || "")}</textarea>
        <small class="mp-note-saved">${t("Сохраняется само · Esc — готово", "Saves by itself · Esc — done")}</small></div>`;
    }
    return `<div class="mp-note" data-font="${esc(b.font || "text")}" data-tsize="${esc(b.text_size || "m")}" data-checklist="${b.checklist !== false}">
      <div class="mp-note-view" tabindex="0" role="button" aria-label="${esc(t("Изменить заметку", "Edit the note"))}" data-note-edit>${empty ? `<p class="mp-note-empty">${t("Нажмите, чтобы написать. «[ ] дело» — флажок, «# заголовок».", "Click to write. “[ ] task” makes a checkbox, “# heading” a heading.")}</p>` : noteRender(note.text, b.checklist !== false)}</div>
      ${note.updated ? `<small class="mp-note-saved" data-mp-ago="${note.updated}">${esc(ago(note.updated))}</small>` : ""}</div>`;
  }

  const PRIO = () => ({ 1: t("Высокий", "High"), 2: t("Средний", "Medium"), 3: t("Низкий", "Low") });
  const REPEAT_NAMES = () => ({ "": t("Не повторять", "Does not repeat"), day: t("Каждый день", "Every day"), weekdays: t("По будням", "On weekdays"),
    week: t("Каждую неделю", "Every week"), month: t("Каждый месяц", "Every month"), year: t("Каждый год", "Every year") });
  const todoUi = (pid) => (state.todoUi[pid] = state.todoUi[pid] || { expanded: false, subs: {} });
  const isoDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  function dueInfo(due) {
    if (!due) return null;
    const day = due.slice(0, 10), time = due.length > 10 ? due.slice(11, 16) : "";
    const today = new Date(); const todayIso = isoDay(today);
    const tomorrow = new Date(today); tomorrow.setDate(today.getDate() + 1);
    const at = new Date(day + "T" + (time || "23:59"));
    let label, stateName = "later";
    if (day < todayIso || (day === todayIso && time && at < today)) { stateName = "overdue"; }
    if (day === todayIso) { label = t("Сегодня", "Today"); if (stateName !== "overdue") stateName = "today"; }
    else if (day === isoDay(tomorrow)) { label = t("Завтра", "Tomorrow"); stateName = stateName === "overdue" ? stateName : "soon"; }
    else {
      const d = new Date(day + "T12:00");
      const diff = Math.round((d - new Date(todayIso + "T12:00")) / 86400000);
      label = diff > 1 && diff < 7 ? d.toLocaleDateString(en() ? "en-GB" : "ru-RU", { weekday: "short" })
        : d.toLocaleDateString(en() ? "en-GB" : "ru-RU", { day: "numeric", month: "short", ...(d.getFullYear() !== today.getFullYear() ? { year: "numeric" } : {}) });
      if (stateName !== "overdue" && diff < 7) stateName = "soon";
    }
    return { label: time ? `${label} ${time}` : label, state: stateName, day, sortKey: day + (time || "99") };
  }
  function todoItems(panel) {
    const o = panel.options || {};
    const lists = panel.todo?.lists || [];
    const tags = String(o.tags || "").split(/[,\s]+/).map((x) => x.replace(/^#/, "").toLowerCase()).filter(Boolean);
    const todayIso = isoDay(new Date());
    const week = new Date(); week.setDate(week.getDate() + 7); const weekIso = isoDay(week);
    const rows = [];
    lists.forEach((lst, li) => (lst.items || []).forEach((it, ii) => rows.push({ ...it, _list: lst, _li: li, _ii: ii })));
    const keep = rows.filter((it) => {
      if (tags.length && !tags.some((x) => (it.tags || []).includes(x))) return false;
      const day = (it.due || "").slice(0, 10);
      switch (o.filter) {
        case "today": return !!day && day <= todayIso;
        case "week": return !!day && day <= weekIso;
        case "overdue": return !!day && dueInfo(it.due)?.state === "overdue";
        case "nodate": return !day;
        case "starred": return it.priority === 1 || it.priority === 2;
        default: return true;
      }
    });
    const cmp = {
      due: (a, b) => (a.due ? dueInfo(a.due).sortKey : "9999") .localeCompare(b.due ? dueInfo(b.due).sortKey : "9999"),
      priority: (a, b) => (a.priority || 9) - (b.priority || 9),
      created: (a, b) => (b.created || 0) - (a.created || 0),
      title: (a, b) => a.title.localeCompare(b.title, en() ? "en" : "ru"),
    }[o.sort];
    const manual = (a, b) => a._li - b._li || a._ii - b._ii;
    keep.sort((a, b) => (cmp ? cmp(a, b) : 0) || manual(a, b));
    return { open: keep.filter((it) => !it.done), done: keep.filter((it) => it.done).sort((a, b) => (b.done_at || 0) - (a.done_at || 0)), lists };
  }
  function todoRowHtml(it, panel, size) {
    const o = panel.options || {};
    const due = o.show_due !== false ? dueInfo(it.due) : null;
    const subs = it.subtasks || [];
    const subsDone = subs.filter((x) => x.done).length;
    const open = todoUi(panel.id).subs[it.id];
    const meta = [];
    if (due) meta.push(`<span class="mp-todo-due" data-state="${due.state}">${ico("calendar")}${esc(due.label)}</span>`);
    if (it.repeat) meta.push(`<span class="mp-todo-flag" title="${esc(REPEAT_NAMES()[it.repeat.every] || "")}">${ico("repeat")}</span>`);
    if (it.remind) meta.push(`<span class="mp-todo-flag" title="${esc(t("Напоминание ", "Reminder ") + it.remind.replace("T", " "))}">${ico("bell")}</span>`);
    if (o.show_subtasks !== false && subs.length) meta.push(`<button type="button" class="mp-todo-subs-btn" data-todo-subs aria-expanded="${!!open}" title="${esc(t("Подзадачи", "Subtasks"))}">${ico("chevron")}${subsDone}/${subs.length}</button>`);
    if (o.show_tags !== false && (it.tags || []).length && size !== "s") meta.push(...it.tags.slice(0, 3).map((tg) => `<span class="mp-todo-tag">#${esc(tg)}</span>`));
    if (it.notes && size !== "s") meta.push(`<span class="mp-todo-flag" title="${esc(it.notes.slice(0, 200))}">${ico("note")}</span>`);
    const subsHtml = open && subs.length ? `<div class="mp-todo-subs">${subs.map((x) => `<label class="mp-todo-sub"><input type="checkbox" data-todo-sub="${esc(x.id)}"${x.done ? " checked" : ""}><span>${esc(x.title)}</span></label>`).join("")}</div>` : "";
    return `<div class="mp-todo-item" data-todo-item="${esc(it.id)}" data-todo-list="${esc(it._list.id)}" data-done="${!!it.done}"${it.priority ? ` data-prio="${it.priority}"` : ""} tabindex="0" role="listitem" aria-label="${esc(it.title)}">
      <button type="button" class="mp-todo-check" role="checkbox" aria-checked="${!!it.done}" data-todo-toggle aria-label="${esc((it.done ? t("Вернуть: ", "Reopen: ") : t("Выполнено: ", "Done: ")) + it.title)}">${ico("check")}</button>
      <div class="mp-todo-main"><span class="mp-todo-title" data-todo-open>${esc(it.title)}</span>${meta.length ? `<span class="mp-todo-meta">${meta.join("")}</span>` : ""}${subsHtml}</div></div>`;
  }
  function todoHtml(b, size, ctx) {
    const panel = ctx.panel;
    if (!panel) return "";
    const o = panel.options || {};
    const ui = todoUi(panel.id);
    const { open, done, lists } = todoItems(panel);
    if (!lists.length) {
      return `<div class="mp-todo" data-empty><p class="mp-text muted">${t("У панели нет списка задач.", "This panel has no task list.")}</p>
        <button type="button" class="mp-btn" data-todo-newlist>${ico("plus")}${t("Создать список", "Create a list")}</button></div>`;
    }
    const sidebar = ctx.mode === "sidebar";
    const cap = ui.expanded ? 500 : sidebar || size === "s" ? 3 : size === "m" ? 6 : ctx.wide ? 16 : 12;
    const quick = b.quick_add !== false && o.quick_add !== false;
    const target = ui.target && lists.some((l) => l.id === ui.target) ? ui.target : lists[0].id;
    const add = quick ? `<div class="mp-todo-add">${ico("plus")}<input class="mp-todo-input" data-todo-add data-mp-keep="todo-add:${esc(panel.id)}" maxlength="300" autocomplete="off" spellcheck="true"
        placeholder="${esc(t("Добавить задачу…", "Add a task…"))}" title="${esc(t("Enter — добавить. Понимает «завтра 18:00», «пт», «25.10», «!1», «#дом», «каждую неделю».", "Enter adds. Understands “tomorrow 6pm”, “fri”, “25.10”, “!1”, “#home”, “every week”."))}" aria-label="${esc(t("Новая задача", "New task"))}" value="${esc(ui.draft || "")}">
        ${lists.length > 1 && !sidebar && size !== "s" ? `<select class="mp-todo-target" data-todo-target aria-label="${esc(t("В список", "To list"))}">${lists.map((l) => `<option value="${esc(l.id)}"${l.id === target ? " selected" : ""}>${esc(l.name)}</option>`).join("")}</select>` : ""}</div>` : "";
    let body = "";
    const shown = open.slice(0, cap);
    const groupMode = o.group === "none" ? "" : o.group === "due" ? "due" : lists.length > 1 ? "list" : "";
    if (!open.length) {
      body = `<p class="mp-todo-none">${ico("check")}${done.length ? t("Всё сделано!", "All done!") : t("Задач пока нет.", "No tasks yet.")}</p>`;
    } else if (groupMode) {
      const groups = new Map();
      shown.forEach((it) => {
        let key, label;
        if (groupMode === "list") { key = it._list.id; label = it._list.name; }
        else {
          const d = dueInfo(it.due);
          key = !d ? "z" : d.state === "overdue" ? "a" : d.state === "today" ? "b" : d.state === "soon" ? "c" : "d";
          label = { a: t("Просрочено", "Overdue"), b: t("Сегодня", "Today"), c: t("Скоро", "Soon"), d: t("Позже", "Later"), z: t("Без срока", "No date") }[key];
        }
        if (!groups.has(key)) groups.set(key, { label, color: groupMode === "list" ? it._list.color : "", rows: [] });
        groups.get(key).rows.push(it);
      });
      const order = [...groups.keys()]; if (groupMode === "due") order.sort();
      body = order.map((k) => { const g = groups.get(k); return `<div class="mp-todo-group"><div class="mp-todo-ghead"${g.color ? ` data-color="${esc(g.color)}"` : ""}>${esc(g.label)}<span>${g.rows.length}</span></div>${g.rows.map((it) => todoRowHtml(it, panel, size)).join("")}</div>`; }).join("");
    } else {
      body = shown.map((it) => todoRowHtml(it, panel, size)).join("");
    }
    const left = open.length - shown.length;
    const showDone = ui.showDone ?? !!o.show_done;
    const doneHtml = showDone && done.length && (size !== "s" || ui.expanded) && !sidebar
      ? `<div class="mp-todo-group done"><div class="mp-todo-ghead">${t("Выполнено", "Completed")}<span>${done.length}</span><button type="button" class="mp-linkbtn" data-todo-clear>${t("Очистить", "Clear")}</button></div>${done.slice(0, ui.expanded ? 100 : 5).map((it) => todoRowHtml(it, panel, size)).join("")}</div>` : "";
    const foot = [
      left > 0 ? `<button type="button" class="mp-linkbtn" data-todo-more>${t(`Ещё ${left}`, `${left} more`)}</button>` : ui.expanded && open.length > 3 ? `<button type="button" class="mp-linkbtn" data-todo-more>${t("Свернуть", "Collapse")}</button>` : "",
      done.length && !sidebar ? `<button type="button" class="mp-linkbtn" data-todo-done aria-pressed="${showDone}">${showDone ? t("Скрыть выполненные", "Hide completed") : t(`Выполненные · ${done.length}`, `Completed · ${done.length}`)}</button>` : "",
    ].filter(Boolean).join("");
    const hint = quick && !sidebar && size !== "s" ? `<small class="mp-todo-hint">${t("«завтра 18:00» · «пт» · «25.10» · «!1» · «#дом» · «каждую неделю» — Enter", "“tomorrow 6pm” · “fri” · “25.10” · “!1” · “#home” · “every week” — Enter")}</small>` : "";
    return `<div class="mp-todo" data-size="${size}">${add}${hint}<div class="mp-todo-list" role="list">${body}</div>${doneHtml}${foot ? `<div class="mp-todo-foot">${foot}</div>` : ""}</div>`;
  }
  function todoFind(lid, iid) {
    for (const p of state.panels) {
      for (const l of p.todo?.lists || []) {
        if (l.id !== lid) continue;
        const it = (l.items || []).find((x) => x.id === iid);
        if (it) return { list: l, item: it };
      }
    }
    return null;
  }

  function todoLocal(lid, fn) {
    for (const p of state.panels) for (const l of p.todo?.lists || []) if (l.id === lid) fn(l);
    rerender((p) => p.kind === "todo");
  }
  async function todoSync() {
    try {
      const out = await call("/api/pg/panels-todo");
      const by = new Map((out.lists || []).map((l) => [l.id, l]));
      state.panels.forEach((p) => { if (p.kind === "todo" && p.todo) p.todo.lists = (p.options?.lists || []).map((id) => by.get(id)).filter(Boolean); if (p.todo) p.todo.all = (out.lists || []).map(({ id, name, color }) => ({ id, name, color })); });
      rerender((p) => p.kind === "todo");
    } catch (error) {  }
  }
  async function todoCall(path, opts) {
    try { return await call(path, opts); }
    catch (error) { notify(error.message || String(error), "err"); await todoSync(); return null; }
  }
  async function todoAdd(pid, input) {
    const panel = byId(pid);
    const title = input.value.trim();
    if (!panel || !title) return;
    const ui = todoUi(pid);
    const lists = panel.todo?.lists || [];
    const lid = ui.target && lists.some((l) => l.id === ui.target) ? ui.target : lists[0]?.id;
    if (!lid) return;
    input.value = "";
    ui.draft = "";
    const out = await todoCall(`/api/pg/panels-todo/${lid}/items`, { method: "POST", body: { title, quick: true, lang: en() ? "en" : "ru", index: panel.options?.sort === "manual" || !panel.options?.sort ? undefined : 0 } });
    if (out?.item) {
      todoLocal(lid, (l) => { if (!l.items.some((x) => x.id === out.item.id)) l.items.push(out.item); });
      const due = dueInfo(out.item.due);
      if (out.item.title !== title && (due || out.item.priority || (out.item.tags || []).length)) {
        notify([t("Добавлено", "Added"), due?.label, out.item.priority ? "!" + out.item.priority : "", ...(out.item.tags || []).map((x) => "#" + x)].filter(Boolean).join(" · "), "info");
      }
      todoSync();
    }
  }
  async function todoToggle(lid, iid) {
    const found = todoFind(lid, iid);
    if (!found) return;
    const next = !found.item.done;
    const row = $$q(`[data-todo-item="${CSS.escape(iid)}"] .mp-todo-check`);
    row.forEach((b) => { b.setAttribute("aria-checked", String(next)); b.closest(".mp-todo-item")?.setAttribute("data-done", String(next)); });
    if (!motionOff() && next) await new Promise((r) => setTimeout(r, 380));
    const out = await todoCall(`/api/pg/panels-todo/${lid}/items/${iid}`, { method: "PATCH", body: { done: next, lang: en() ? "en" : "ru" } });
    if (!out?.item) return;
    todoLocal(lid, (l) => { const i = l.items.findIndex((x) => x.id === iid); if (i >= 0) l.items[i] = out.item; });
    if (out.item.advanced) notify(t(`Следующий раз: ${dueInfo(out.item.due)?.label || out.item.due}`, `Next time: ${dueInfo(out.item.due)?.label || out.item.due}`), "info");
    else if (next && typeof toast === "function") {
      toast(t(`«${out.item.title}» выполнена`, `“${out.item.title}” done`), "info", { label: t("Вернуть", "Undo"), run: () => todoToggle(lid, iid) });
    }
  }
  async function todoDelete(lid, iid) {
    const found = todoFind(lid, iid);
    if (!found) return;
    const index = found.list.items.indexOf(found.item);
    todoLocal(lid, (l) => { l.items = l.items.filter((x) => x.id !== iid); });
    const out = await todoCall(`/api/pg/panels-todo/${lid}/items/${iid}`, { method: "DELETE" });
    if (out?.item && typeof toast === "function") {
      toast(t(`Задача «${out.item.title}» удалена`, `Task “${out.item.title}” deleted`), "info", { label: t("Вернуть", "Undo"), run: async () => {
        const back = await todoCall(`/api/pg/panels-todo/${lid}/restore`, { method: "POST", body: { item: out.item, index } });
        if (back) todoSync();
      } });
    }
  }
  async function todoPatch(lid, iid, body) {
    const out = await todoCall(`/api/pg/panels-todo/${lid}/items/${iid}`, { method: "PATCH", body: { ...body, lang: en() ? "en" : "ru" } });
    if (out?.item) todoLocal(lid, (l) => { const i = l.items.findIndex((x) => x.id === iid); if (i >= 0) l.items[i] = out.item; });
    return out?.item || null;
  }
  async function todoMove(lid, iid, delta) {
    const found = todoFind(lid, iid);
    if (!found) return;
    const index = found.list.items.indexOf(found.item) + delta;
    if (index < 0 || index >= found.list.items.length) return;
    todoLocal(lid, (l) => { const i = l.items.findIndex((x) => x.id === iid); const [it] = l.items.splice(i, 1); l.items.splice(index, 0, it); });
    requestAnimationFrame(() => $q(`[data-todo-item="${CSS.escape(iid)}"]`)?.focus({ preventScroll: true }));
    await todoCall(`/api/pg/panels-todo/${lid}/items/${iid}`, { method: "PATCH", body: { index } });
  }
  async function todoNewList(pid, name) {
    const out = await todoCall("/api/pg/panels-todo", { method: "POST", body: { name: name || t("Задачи", "Tasks") } });
    if (!out?.list) return null;
    const panel = byId(pid);
    if (panel) {
      const lists = [...(panel.options?.lists || []), out.list.id];
      await patch(pid, { options: { lists } });
    }
    return out.list;
  }
  function todoEditorHtml(lid, it, allLists) {
    const day = (it.due || "").slice(0, 10), time = (it.due || "").length > 10 ? it.due.slice(11, 16) : "";
    return `<div class="mp-todo-edit" role="dialog" aria-label="${esc(t("Задача", "Task"))}" data-edit-list="${esc(lid)}" data-edit-item="${esc(it.id)}">
      <div class="mp-detail-head">${ico("todo")}<input class="mp-input mp-todo-etitle" data-te="title" value="${esc(it.title)}" maxlength="300" aria-label="${esc(t("Название", "Title"))}">
        <button type="button" class="mp-x" data-mp-pop-close aria-label="${t("Закрыть", "Close")}">${ico("close")}</button></div>
      <div class="mp-todo-egrid">
        <label class="mp-field"><span>${t("Срок", "Due")}</span><div class="mp-inline-row"><input class="mp-input" type="date" data-te="day" value="${esc(day)}"><input class="mp-input" type="time" data-te="time" value="${esc(time)}"${day ? "" : " disabled"}></div></label>
        <div class="mp-field"><span>${t("Приоритет", "Priority")}</span><div class="mp-seg" data-te="priority">${[0, 1, 2, 3].map((n) => `<button type="button" data-v="${n}" aria-pressed="${(it.priority || 0) === n}"${n ? ` data-prio="${n}"` : ""}>${n ? "!" + n : t("Нет", "None")}</button>`).join("")}</div></div>
        <label class="mp-field"><span>${t("Повтор", "Repeat")}</span><select class="mp-input" data-te="repeat" data-no-search>${Object.entries(REPEAT_NAMES()).map(([k, v]) => `<option value="${k}"${(it.repeat?.every || "") === k ? " selected" : ""}>${esc(v)}</option>`).join("")}</select></label>
        <label class="mp-field"><span>${t("Напомнить", "Remind")}</span><input class="mp-input" type="datetime-local" data-te="remind" value="${esc(it.remind || "")}"><small>${t("Звук и уведомление Windows — как «напомни мне…» в «Быстром доступе» (там же выбирается Telegram).", "Sound and a Windows notification — like “remind me…” in Quick Ask (Telegram is chosen there).")}</small></label>
        <label class="mp-field"><span>${t("Теги", "Tags")}</span><input class="mp-input" data-te="tags" value="${esc((it.tags || []).join(", "))}" placeholder="${esc(t("работа, дом", "work, home"))}"></label>
        ${allLists.length > 1 ? `<label class="mp-field"><span>${t("Список", "List")}</span><select class="mp-input" data-te="list" data-no-search>${allLists.map((l) => `<option value="${esc(l.id)}"${l.id === lid ? " selected" : ""}>${esc(l.name)}</option>`).join("")}</select></label>` : ""}
      </div>
      <label class="mp-field"><span>${t("Заметки", "Notes")}</span><textarea class="mp-input" data-te="notes" rows="2" maxlength="2000">${esc(it.notes || "")}</textarea></label>
      <div class="mp-field"><span>${t("Подзадачи", "Subtasks")}</span><div class="mp-todo-esubs">${(it.subtasks || []).map((x) => `<div class="mp-todo-esub" data-sub="${esc(x.id)}"><input type="checkbox"${x.done ? " checked" : ""} data-te-sub-done aria-label="${esc(x.title)}"><input class="mp-input" data-te-sub-title value="${esc(x.title)}" maxlength="200"><button type="button" class="mp-x" data-te-sub-del aria-label="${t("Убрать", "Remove")}">${ico("close")}</button></div>`).join("")}
        <div class="mp-todo-esub add">${ico("plus")}<input class="mp-input" data-te-sub-add placeholder="${esc(t("Подзадача — Enter", "Subtask — Enter"))}" maxlength="200"></div></div></div>
      <div class="mp-detail-foot mp-todo-efoot">${it.completed_count ? `<span>${esc(t(`Выполнено раз: ${it.completed_count}`, `Completed ${it.completed_count}×`))}</span>` : ""}<span class="grow"></span>
        <button type="button" class="mp-btn" data-te-del>${ico("trash")}${t("Удалить", "Delete")}</button><button type="button" class="mp-btn primary" data-mp-pop-close>${t("Готово", "Done")}</button></div></div>`;
  }
  function readTodoEditor(el) {
    const v = (k) => $q(`[data-te="${k}"]`, el);
    const day = pickedValue(v("day")), time = pickedValue(v("time"));
    const subtasks = $$q(".mp-todo-esub[data-sub]", el).map((row) => ({ id: row.dataset.sub, title: $q("[data-te-sub-title]", row).value.trim(), done: $q("[data-te-sub-done]", row).checked })).filter((x) => x.title);
    return {
      title: v("title").value.trim(), due: day ? day + (time ? "T" + time : "") : "",
      priority: +($q('[data-te="priority"] [aria-pressed=true]', el)?.dataset.v || 0), repeat: v("repeat").value ? { every: v("repeat").value } : null,
      remind: v("remind").value || "", tags: v("tags").value, notes: v("notes").value, subtasks,
    };
  }
  function openTodoEditor(pid, lid, iid, anchor) {
    const found = todoFind(lid, iid);
    const panel = byId(pid);
    if (!found || !panel) return;
    closePop(); closeMenu();
    const el = document.createElement("div");
    el.className = "mp-pop mp-pop-detail mp-todo-pop";
    el.innerHTML = todoEditorHtml(lid, found.item, panel.todo?.all || panel.todo?.lists || []);
    document.body.appendChild(el);
    pickers(el);
    position(el, anchor.getBoundingClientRect(), { align: "start" });
    state.pop = { el, anchor, id: "__todo:" + iid };
    let timer = 0;
    let cur = { lid };
    const save = async () => {
      clearTimeout(timer);
      const body = readTodoEditor(el);
      if (!body.title) return;
      const moveTo = $q('[data-te="list"]', el)?.value;
      const item = await todoPatch(cur.lid, iid, body);
      if (item && moveTo && moveTo !== cur.lid) {
        await todoCall(`/api/pg/panels-todo/${cur.lid}/items/${iid}`, { method: "PATCH", body: { move_to: moveTo, index: -1 } });
        cur = { lid: moveTo };
        el.querySelector(".mp-todo-edit").dataset.editList = moveTo;
        todoSync();
      }
    };
    const later = () => { clearTimeout(timer); timer = setTimeout(save, 450); };
    el.addEventListener("input", (event) => {
      if (event.target.matches("[data-te-sub-add]")) return;
      if (event.target.matches('[data-te="day"]')) { const tm = $q('[data-te="time"]', el); tm.disabled = !event.target.value; if (!event.target.value) tm.value = ""; }
      later();
    });
    el.addEventListener("change", (event) => { if (!event.target.matches("[data-te-sub-add]")) save(); });
    el.addEventListener("click", (event) => {
      const seg = event.target.closest('[data-te="priority"] button');
      if (seg) { $$q("button", seg.parentElement).forEach((b) => b.setAttribute("aria-pressed", String(b === seg))); save(); return; }
      const del = event.target.closest("[data-te-sub-del]");
      if (del) { del.closest(".mp-todo-esub").remove(); save(); return; }
      if (event.target.closest("[data-te-del]")) { closePop(); todoDelete(cur.lid, iid); return; }
      if (event.target.closest("[data-mp-pop-close]")) { save(); closePop(); anchor?.focus?.({ preventScroll: true }); }
    });
    el.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && event.target.matches("[data-te-sub-add]")) {
        event.preventDefault();
        const value = event.target.value.trim();
        if (!value) return;
        const row = document.createElement("div");
        row.className = "mp-todo-esub"; row.dataset.sub = "s_" + Math.random().toString(36).slice(2, 10);
        row.innerHTML = `<input type="checkbox" data-te-sub-done aria-label="${esc(value)}"><input class="mp-input" data-te-sub-title value="${esc(value)}" maxlength="200"><button type="button" class="mp-x" data-te-sub-del aria-label="${t("Убрать", "Remove")}">${ico("close")}</button>`;
        event.target.closest(".mp-todo-esub").before(row);
        event.target.value = "";
        save();
      } else if (event.key === "Enter" && event.target.matches('[data-te="title"]')) { event.preventDefault(); save(); closePop(); anchor?.focus?.(); }
      else if (event.key === "Escape") { event.stopPropagation(); save(); closePop(); anchor?.focus?.({ preventScroll: true }); }
    });
    requestAnimationFrame(() => $q('[data-te="title"]', el)?.focus({ preventScroll: true }));
  }
  function bindTodo() {
    document.addEventListener("click", (event) => {
      const box = event.target.closest(".mp-todo");
      if (!box) return;
      const card = box.closest("[data-panel-id]");
      const pid = card?.dataset.panelId;
      if (!pid) return;
      const row = event.target.closest("[data-todo-item]");
      if (event.target.closest("[data-todo-toggle]") && row) { event.stopPropagation(); todoToggle(row.dataset.todoList, row.dataset.todoItem); return; }
      if (event.target.closest("[data-todo-subs]") && row) { const ui = todoUi(pid); ui.subs[row.dataset.todoItem] = !ui.subs[row.dataset.todoItem]; rerender((p) => p.id === pid); return; }
      if (event.target.closest("[data-todo-open]") && row) { openTodoEditor(pid, row.dataset.todoList, row.dataset.todoItem, row); return; }
      if (event.target.closest("[data-todo-more]")) { const ui = todoUi(pid); ui.expanded = !ui.expanded; rerender((p) => p.id === pid); return; }
      if (event.target.closest("[data-todo-done]")) { const ui = todoUi(pid); const p = byId(pid); ui.showDone = !(ui.showDone ?? !!p?.options?.show_done); rerender((x) => x.id === pid); return; }
      if (event.target.closest("[data-todo-clear]")) {
        const p = byId(pid);
        (p?.todo?.lists || []).forEach(async (l) => { await todoCall(`/api/pg/panels-todo/${l.id}/clear-done`, { method: "POST" }); todoSync(); });
        return;
      }
      if (event.target.closest("[data-todo-newlist]")) { todoNewList(pid); }
    });
    document.addEventListener("change", (event) => {
      const sub = event.target.closest("[data-todo-sub]");
      if (sub) {
        const row = sub.closest("[data-todo-item]");
        if (row) todoPatch(row.dataset.todoList, row.dataset.todoItem, { toggle_subtask: sub.dataset.todoSub });
        return;
      }
      const target = event.target.closest("[data-todo-target]");
      if (target) { const pid = target.closest("[data-panel-id]")?.dataset.panelId; if (pid) todoUi(pid).target = target.value; }
    });
    document.addEventListener("keydown", (event) => {
      const input = event.target.closest?.("[data-todo-add]");
      if (input) {
        const pid = input.closest("[data-panel-id]")?.dataset.panelId;
        if (event.key === "Enter" && !event.isComposing) { event.preventDefault(); todoAdd(pid, input); }
        else if (event.key === "Escape") { input.value = ""; input.blur(); }
        else if (event.key === "ArrowDown") { const first = input.closest(".mp-todo")?.querySelector("[data-todo-item]"); if (first) { event.preventDefault(); first.focus(); } }
        event.stopPropagation();
        return;
      }
      const row = event.target.closest?.("[data-todo-item]");
      if (!row || event.target !== row) return;
      const pid = row.closest("[data-panel-id]")?.dataset.panelId;
      const lid = row.dataset.todoList, iid = row.dataset.todoItem;
      const rows = $$q("[data-todo-item]", row.closest(".mp-todo"));
      const i = rows.indexOf(row);
      if (event.key === " " || event.key === "Spacebar") { event.preventDefault(); todoToggle(lid, iid); }
      else if (event.key === "Enter" || event.key === "F2") { event.preventDefault(); openTodoEditor(pid, lid, iid, row); }
      else if (event.key === "Delete" || event.key === "Backspace") { event.preventDefault(); event.stopPropagation(); rows[i + 1]?.focus() || rows[i - 1]?.focus(); todoDelete(lid, iid); }
      else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const p = byId(pid);
        if (event.altKey && (!p?.options?.sort || p.options.sort === "manual")) todoMove(lid, iid, event.key === "ArrowDown" ? 1 : -1);
        else if (event.key === "ArrowUp" && i === 0) row.closest(".mp-todo")?.querySelector("[data-todo-add]")?.focus();
        else rows[i + (event.key === "ArrowDown" ? 1 : -1)]?.focus();
      } else if (event.key === "ArrowRight" && row.querySelector("[data-todo-subs]")) { const ui = todoUi(pid); if (!ui.subs[iid]) { ui.subs[iid] = true; rerender((p) => p.id === pid); } }
      else if (event.key === "ArrowLeft") { const ui = todoUi(pid); if (ui.subs[iid]) { ui.subs[iid] = false; rerender((p) => p.id === pid); } }
      else return;
      event.stopPropagation();
    });
  }

  const PHASES = () => ({ work: t("Работа", "Focus"), short_break: t("Короткий перерыв", "Short break"), long_break: t("Длинный перерыв", "Long break") });
  function serverNow() {
    const d = state.timers;
    return d ? (d.snap.now || Date.now() / 1000) + (Date.now() - d.at) / 1000 : Date.now() / 1000;
  }
  const mmss = (sec) => { sec = Math.max(0, Math.round(sec)); const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60; return (h ? h + ":" + String(m).padStart(2, "0") : String(m)) + ":" + String(s).padStart(2, "0"); };
  function timerLeft(tm) { return tm.paused ? +tm.remaining || 0 : Math.max(0, (+tm.ends_at || 0) - serverNow()); }
  function pomoLeft(p) { if (!p?.active) return 0; if (p.waiting) return 0; return p.paused_remaining != null ? +p.paused_remaining : Math.max(0, (+p.ends_at || 0) - serverNow()); }
  function timersHtml(b, size, ctx) {
    const d = state.timers;
    if (!d) return `<div class="mp-building-note"><span class="mp-spin"></span>${t("Загружаю таймеры…", "Loading timers…")}</div>`;
    if (!d.available) return `<div class="mp-wall">${ico("alert")}<span>${esc(t("Таймеры «Быстрого доступа» недоступны в этом окне.", "Quick Ask timers are unavailable in this window."))}</span></div>`;
    const parts = b.parts || ["pomodoro", "timers", "stopwatch"];
    const controls = b.controls !== false && ctx.mode !== "inline";
    const small = size === "s" || ctx.mode === "sidebar";
    const out = [];
    const snap = d.snap || {};
    if (parts.includes("pomodoro")) {
      const p = snap.pomodoro || {};
      if (p.active) {
        const total = +p.total || (+p[p.phase] || 25) * 60;
        const left = pomoLeft(p);
        const pct = total ? Math.max(0, Math.min(100, (1 - left / total) * 100)) : 0;
        const r = 15.5, c = 2 * Math.PI * r;
        const paused = p.paused_remaining != null;
        out.push(`<div class="mp-pomo" data-phase="${esc(p.phase || "work")}"${paused ? " data-paused" : ""}${p.waiting ? " data-waiting" : ""}>
          <div class="mp-gauge"><svg viewBox="0 0 40 40" aria-hidden="true"><circle class="trk" cx="20" cy="20" r="${r}"/>${pct > 0 ? `<circle class="val" cx="20" cy="20" r="${r}" stroke-dasharray="${(c * pct / 100).toFixed(2)} ${c.toFixed(2)}"/>` : ""}</svg><b data-pomo-left>${p.waiting ? "✓" : esc(mmss(left))}</b></div>
          <div class="mp-pomo-text"><b>${esc(PHASES()[p.phase] || p.phase)}</b><small>${esc(p.waiting ? t("Фаза закончилась", "Phase finished") : paused ? t("На паузе", "Paused") : t(`Цикл ${p.cycle || 1} из ${p.cycles || 4}`, `Cycle ${p.cycle || 1} of ${p.cycles || 4}`))}</small>
          ${controls ? `<div class="mp-tm-btns">${p.waiting ? `<button type="button" class="mp-btn primary" data-tm="pomodoro:resume">${ico("play")}${t("Дальше", "Continue")}</button>`
            : paused ? `<button type="button" class="mp-iconbtn" data-tm="pomodoro:resume" title="${t("Продолжить", "Resume")}" aria-label="${t("Продолжить", "Resume")}">${ico("play")}</button>`
              : `<button type="button" class="mp-iconbtn" data-tm="pomodoro:pause" title="${t("Пауза", "Pause")}" aria-label="${t("Пауза", "Pause")}">${ico("pause")}</button>`}
            <button type="button" class="mp-iconbtn" data-tm="pomodoro:skip" title="${t("Следующая фаза", "Next phase")}" aria-label="${t("Следующая фаза", "Next phase")}">${ico("skip")}</button>
            <button type="button" class="mp-iconbtn" data-tm="pomodoro:stop" title="${t("Остановить", "Stop")}" aria-label="${t("Остановить", "Stop")}">${ico("stop")}</button></div>` : ""}</div></div>`);
      } else if (!small || parts.length === 1) {
        const work = +(d.pomodoro?.work || 25);
        out.push(`<div class="mp-pomo idle"><div class="mp-pomo-text"><b>${t("Помодоро", "Pomodoro")}</b><small>${t(`${work} мин работы, потом перерыв`, `${work} min of focus, then a break`)}</small></div>
          ${controls ? `<button type="button" class="mp-btn primary" data-tm="pomodoro:start">${ico("play")}${t("Начать", "Start")}</button>` : ""}</div>`);
      }
      if (b.stats !== false && !small && snap.stats?.today) {
        const st = snap.stats;
        out.push(`<div class="mp-tm-stats">${esc(t(`Сегодня: ${st.today.sessions} · ${st.today.minutes} мин`, `Today: ${st.today.sessions} · ${st.today.minutes} min`))}<span class="sep">·</span>${esc(t(`за неделю: ${st.week.sessions}`, `this week: ${st.week.sessions}`))}</div>`);
      }
    }
    if (parts.includes("timers")) {
      const timers = (snap.timers || []).filter((x) => !x.done).slice(0, small ? 2 : 6);
      if (timers.length) {
        out.push(`<div class="mp-tm-list">${timers.map((tm) => {
          const isTimer = tm.kind === "timer";
          const when = isTimer ? mmss(timerLeft(tm)) : new Date(+tm.ends_at * 1000).toLocaleTimeString(en() ? "en-GB" : "ru-RU", { hour: "2-digit", minute: "2-digit" });
          const kind = { timer: t("Таймер", "Timer"), alarm: t("Будильник", "Alarm"), reminder: t("Напоминание", "Reminder") }[tm.kind] || tm.kind;
          return `<div class="mp-tm-row" data-tm-id="${esc(tm.id)}"${tm.paused ? " data-paused" : ""}>${ico(isTimer ? "timer" : tm.kind === "alarm" ? "bell" : "clock")}<span class="mp-tm-label"><b>${esc(tm.label || kind)}</b><small>${esc(tm.label ? kind : "")}</small></span>
            <b class="mp-tm-left"${isTimer ? ` data-tm-left="${esc(tm.id)}"` : ""}>${esc(when)}</b>${controls ? `${isTimer ? `<button type="button" class="mp-iconbtn" data-tm="${tm.paused ? "resume" : "pause"}" data-tm-ref="${esc(tm.id)}" aria-label="${tm.paused ? t("Продолжить", "Resume") : t("Пауза", "Pause")}">${ico(tm.paused ? "play" : "pause")}</button>` : ""}<button type="button" class="mp-iconbtn" data-tm="cancel" data-tm-ref="${esc(tm.id)}" aria-label="${t("Отменить", "Cancel")}">${ico("close")}</button>` : ""}</div>`;
        }).join("")}</div>`);
      } else if (!small) {
        out.push(`<div class="mp-tm-empty"><small>${t("Таймеров нет", "No timers")}</small>${controls ? [5, 15, 25].map((m) => `<button type="button" class="mp-chipbtn" data-tm="add" data-tm-sec="${m * 60}">+${m} ${t("мин", "min")}</button>`).join("") : ""}</div>`);
      }
    }
    if (parts.includes("stopwatch")) {
      const sw = snap.stopwatch || {};
      const value = sw.running ? (+sw.elapsed || 0) + serverNow() - (+sw.started_at || serverNow()) : +sw.value || +sw.elapsed || 0;
      if (sw.running || value > 0 || (!small && parts.length <= 2)) {
        out.push(`<div class="mp-tm-row sw">${ico("timer")}<span class="mp-tm-label"><b>${t("Секундомер", "Stopwatch")}</b>${(sw.laps || []).length ? `<small>${esc(t(`кругов: ${sw.laps.length}`, `${sw.laps.length} laps`))}</small>` : ""}</span>
          <b class="mp-tm-left" data-sw-left>${esc(mmss(value))}</b>${controls ? `<button type="button" class="mp-iconbtn" data-tm="stopwatch:${sw.running ? "stop" : "start"}" aria-label="${sw.running ? t("Стоп", "Stop") : t("Старт", "Start")}">${ico(sw.running ? "pause" : "play")}</button>${sw.running ? `<button type="button" class="mp-iconbtn" data-tm="stopwatch:lap" aria-label="${t("Круг", "Lap")}">${ico("flag2")}</button>` : value > 0 ? `<button type="button" class="mp-iconbtn" data-tm="stopwatch:reset" aria-label="${t("Сбросить", "Reset")}">${ico("refresh")}</button>` : ""}` : ""}</div>`);
      }
    }
    return `<div class="mp-timers">${out.join("") || `<p class="mp-text muted">${t("Сейчас ничего не идёт.", "Nothing is running.")}</p>`}</div>`;
  }
  async function loadTimers() {
    try {
      const out = await call("/api/pg/panels-timers");
      state.timers = { snap: out.timers || {}, pomodoro: out.pomodoro || {}, available: out.available !== false, at: Date.now() };
    } catch (error) {
      state.timers = { snap: {}, available: false, at: Date.now() };
    }
  }
  async function timerAct(btn) {
    const [op, action] = String(btn.dataset.tm).split(":");
    const body = { op };
    if (action) body.action = action;
    if (btn.dataset.tmRef) body.id = btn.dataset.tmRef;
    if (btn.dataset.tmSec) { body.seconds = +btn.dataset.tmSec; body.label = ""; }
    btn.disabled = true;
    try {
      const out = await call("/api/assistant/timers", { method: "POST", body });
      if (out?.timers) state.timers = { ...(state.timers || {}), snap: out.timers, available: true, at: Date.now() };
    } catch (error) { notify(error.message || String(error), "err"); }
    rerender((p) => p.kind === "timers");
  }

  function tickTimers() {
    const d = state.timers;
    if (!d?.snap) return;
    const p = d.snap.pomodoro;
    let refresh = false;
    $$q("[data-pomo-left]").forEach((el) => { if (p?.active && !p.waiting) { const left = pomoLeft(p); el.textContent = mmss(left); if (left <= 0) refresh = true; } });
    (d.snap.timers || []).forEach((tm) => $$q(`[data-tm-left="${CSS.escape(tm.id)}"]`).forEach((el) => { const left = timerLeft(tm); el.textContent = mmss(left); if (left <= 0 && !tm.paused) refresh = true; }));
    const sw = d.snap.stopwatch;
    if (sw?.running) $$q("[data-sw-left]").forEach((el) => { el.textContent = mmss((+sw.elapsed || 0) + serverNow() - (+sw.started_at || serverNow())); });
    if (refresh && !state.timersRefreshing) { state.timersRefreshing = true; loadTimers().then(() => { state.timersRefreshing = false; rerender((x) => x.kind === "timers"); }); }
  }

  const LIST_CAP = { s: 2, m: 4, l: 8 };
  const FEED_CAP = { s: 2, m: 4, l: 6 };
  function listCap(b, size) {
    const base = (b.style === "feed" ? FEED_CAP : LIST_CAP)[size] || 4;
    return Math.min(b.max || 20, base);
  }
  function itemMeta(row, data) {
    const bits = [];
    if (row.source) bits.push(`<span class="mp-src">${esc(text(row.source, data))}</span>`);
    if (row.ts) bits.push(`<span data-mp-ago="${+row.ts}">${esc(ago(+row.ts))}</span>`);
    else if (row.subtitle) bits.push(`<span>${esc(text(row.subtitle, data))}</span>`);
    if (row.badge) bits.push(`<span class="mp-tagline" data-tone="${esc(row.tone || "neutral")}">${esc(text(row.badge, data))}</span>`);
    return bits.join('<span class="sep">·</span>');
  }
  function rowHtml(row, data, { feed = false, summary = false, meta: showMeta = true } = {}) {
    row = typeof row === "object" && row ? row : { title: row };
    const url = /^https?:\/\//i.test(String(row.url || "")) ? row.url : "";
    const meta = !showMeta ? "" : feed ? itemMeta(row, data) : (row.subtitle ? esc(text(row.subtitle, data)) : "");
    const inner = `<span class="mp-row-text"><b>${esc(text(row.title, data))}</b>${meta ? `<small>${meta}</small>` : ""}${summary && row.summary ? `<span class="mp-row-sum">${esc(text(row.summary, data))}</span>` : ""}</span>${row.value ? `<span class="mp-row-value">${esc(text(row.value, data))}</span>` : ""}`;
    return url ? `<a class="mp-row" href="${esc(url)}" target="_blank" rel="noopener noreferrer" draggable="false"${row.why ? ` title="${esc(text(row.why, data))}"` : ""}>${inner}</a>` : `<div class="mp-row">${inner}</div>`;
  }
  function listHtml(b, data, size, ctx) {
    const items = raw(b.items, data);
    const label = b.label ? `<div class="mp-list-label">${esc(text(b.label, data))}</div>` : "";
    if (!Array.isArray(items) || !items.length) return `${label}<p class="mp-text muted mp-empty-list">${esc(text(b.empty, data) || t("Пока пусто", "Nothing yet"))}</p>`;
    const feed = b.style === "feed";
    let cap = listCap(b, size);

    if (items.length === cap + 1 && ctx.mode !== "sidebar" && !(b.max && b.max <= cap)) cap += 1;
    const wantSummary = b.summary === "always" ? size !== "s" : b.summary === "never" ? false : size === "l";
    const rows = items.slice(0, cap).map((it) => rowHtml(it, data, { feed, summary: (feed || b.desc) && wantSummary, meta: b.meta !== false })).join("");
    const left = items.length - cap;
    const index = ctx.blockIndex ?? -1;
    const more = left > 0 ? `<button type="button" class="mp-list-more" data-mp-list="${index}" aria-haspopup="dialog">${ico("list")}<span>${t(`Ещё ${left}`, `${left} more`)}</span></button>`
      : (feed && ctx.mode !== "sidebar" ? `<button type="button" class="mp-list-more ghost" data-mp-list="${index}" aria-haspopup="dialog">${ico("info")}<span>${t("Подробнее", "Details")}</span></button>` : "");
    return `<div class="mp-list${feed ? " mp-feed" : ""}${b.style === "compact" ? " mp-compact" : ""}">${label}${rows}${more}</div>`;
  }

  function gaugeHtml(b, data, size) {
    const rawValue = raw(b.value, data);
    const num = Number(String(rawValue ?? "").replace(",", "."));
    const max = Number(String(raw(b.max, data) ?? 100).replace(",", ".")) || 100;
    const known = rawValue !== null && rawValue !== undefined && rawValue !== "" && Number.isFinite(num);
    if (!known && b.optional) return "";
    const pct = known ? Math.max(0, Math.min(100, (num / max) * 100)) : 0;
    const tone = !known ? "none" : pct >= 90 ? "danger" : pct >= 70 ? "warn" : "ok";
    const r = 15.5, c = 2 * Math.PI * r;
    const label = text(b.label, data);
    const caption = size !== "s" ? text(b.caption, data) : "";
    const unit = b.unit != null ? text(b.unit, data) : "%";
    return `<div class="mp-gauge" data-tone="${tone}" role="img" aria-label="${esc(label)}: ${known ? Math.round(num) + unit : t("нет данных", "no data")}">
      <svg viewBox="0 0 40 40" aria-hidden="true"><circle class="trk" cx="20" cy="20" r="${r}"/>${pct > 0 ? `<circle class="val" cx="20" cy="20" r="${r}" stroke-dasharray="${(c * pct / 100).toFixed(2)} ${c.toFixed(2)}"/>` : ""}</svg>
      <b${known && String(Math.round(num)).length >= 3 ? " data-long" : ""}>${known ? esc(Math.round(num)) : "—"}${known && unit ? `<small>${esc(unit)}</small>` : ""}</b>
      <span class="mp-gauge-label">${esc(label)}</span>${caption ? `<span class="mp-gauge-cap" title="${esc(caption)}">${esc(caption)}</span>` : ""}</div>`;
  }

  function limitHtml(l, compact) {
    const used = Math.max(0, Math.min(100, +l.used || 0));
    const now = Date.now() / 1000;
    let elapsed = null;
    if (l.start && l.resets_at && l.resets_at > l.start) elapsed = Math.max(0, Math.min(1, (now - l.start) / (l.resets_at - l.start)));
    const marker = elapsed == null ? 0 : elapsed;
    const pace = used >= 99.5 ? "full" : used / 100 > marker + 0.02 ? "ahead" : "ok";
    const reset = l.resets_at ? `<small class="mp-lim-reset" data-mp-reset="${+l.resets_at}">${esc(t("сброс через ", "resets in ") + untilText(l.resets_at))}<span class="sep">·</span>${esc(clockText(l.resets_at))}</small>`
      : `<small class="mp-lim-reset">${t("время сброса не указано", "reset time unavailable")}</small>`;
    const paceTip = elapsed == null ? "" : t(`Прошло ${Math.round(marker * 100)}% окна`, `${Math.round(marker * 100)}% of the window elapsed`);
    return `<div class="mp-lim${compact ? " compact" : ""}" data-pace="${pace}"><div class="mp-lim-top"><span>${esc(l.title)}</span><b>${Math.round(used)}<small>%</small></b></div>
      <div class="mp-lim-bar" title="${esc(paceTip)}"><i style="width:${used.toFixed(1)}%"></i>${elapsed != null ? `<em style="left:${(marker * 100).toFixed(1)}%"></em>` : ""}</div>${l.detail ? `<small class="mp-lim-reset">${esc(l.detail)}</small>` : ""}${compact ? "" : reset}</div>`;
  }
  const windowGroup = (l) => {
    const key = String(l.key || "").toLowerCase();
    if (/five|5h|primary|session/.test(key) || /5 ?ч|5 ?h/i.test(String(l.title || ""))) return "five_hour";
    if (/seven|week|secondary/.test(key) || /недел|week/i.test(String(l.title || ""))) return "weekly";
    return "other";
  };
  function usageHtml(accounts, size, ctx, b = {}) {
    if (!Array.isArray(accounts) || !accounts.length) return `<p class="mp-text muted">${t("Аккаунтов пока нет.", "No accounts yet.")}</p>`;
    const compact = size === "s" || ctx.mode === "sidebar";
    const windows = Array.isArray(b.windows) && b.windows.length ? b.windows : null;
    const peak = (a) => Math.max(0, ...(a.limits || []).map((l) => +l.used || 0));
    const list = b.sort === "fullest" ? accounts.slice().sort((x, y) => peak(y) - peak(x)) : accounts;
    return `<div class="mp-usage" data-count="${accounts.length}"${b.reset === false ? ' data-no-reset' : ""}${b.pace === false ? ' data-no-pace' : ""}>${list.map((a) => {
      const prov = PROVIDERS[a.provider] || { name: a.provider, icon: "gauge" };
      const limits = (Array.isArray(a.limits) ? a.limits : []).filter((l) => !windows || windows.includes(windowGroup(l)));
      const shown = compact ? limits.slice().sort((x, y) => (y.used || 0) - (x.used || 0)).slice(0, 1) : limits;
      const state = a.state === "stale" ? `<span class="mp-acc-state" title="${esc(a.wall?.message || "")}">${t("устарело", "stale")}</span>` : "";
      const wall = a.state === "wall" || (!limits.length && a.wall)
        ? `<div class="mp-acc-wall">${ico("alert")}<span>${esc(a.wall?.message || t("Нет данных", "No data"))}</span></div>` : "";
      return `<div class="mp-acc" data-provider="${esc(a.provider)}" data-state="${esc(a.state || "")}"><div class="mp-acc-head"><span class="mp-acc-ico">${ico(prov.icon)}</span><b title="${esc(a.name)}">${esc(a.name || prov.name)}</b>${a.plan ? `<span class="mp-plan">${esc(a.plan)}</span>` : ""}${state}</div>
        ${a.state === "wall" ? wall : shown.map((l) => limitHtml(l, compact)).join("")}${a.notice ? `<small class="mp-acc-notice">${esc(a.notice)}</small>` : ""}</div>`;
    }).join("")}</div>`;
  }

  function progressHtml(panel) {
    const p = panel.progress;
    if (panel.source?.type === "feed") {
      const at = Math.max(0, STAGES.indexOf(p?.stage || "plan"));
      return `<ol class="mp-steps" aria-live="polite">${STAGES.map((st, i) => `<li data-state="${i < at ? "done" : i === at ? "now" : "todo"}">${i < at ? ico("check") : i === at ? '<span class="mp-spin"></span>' : '<i></i>'}<span>${esc(i === at ? stageText(p || { stage: st }) : stageText({ stage: st }))}</span></li>`).join("")}</ol>`;
    }
    return `<div class="mp-building-note"><span class="mp-spin"></span>${esc(p ? stageText(p) : panel.source?.type === "ai" ? t("ИИ собирает данные…", "AI is gathering data…") : t("Загружаю…", "Loading…"))}</div>`;
  }

  function localData(panel) {
    const out = { panel_title: panel.title };
    const o = panel.options || {};
    if (panel.kind === "todo") {
      const { open } = todoItems(panel);
      out.open_count = String(open.length);
      const overdue = open.filter((it) => dueInfo(it.due)?.state === "overdue").length;
      const today = open.filter((it) => dueInfo(it.due)?.state === "today").length;
      out.todo_subtitle = [t(`${open.length} открыто`, `${open.length} open`), today ? t(`на сегодня ${today}`, `${today} today`) : "",
        overdue ? t(`просрочено ${overdue}`, `${overdue} overdue`) : ""].filter(Boolean).join(" · ");
    }
    if (panel.kind === "notes") {
      const first = String(panel.note?.text || "").split("\n").find((l) => l.trim()) || "";
      out.note_first = first.replace(/^\s*[-*]?\s*\[( |x|х)\]\s?/i, "").replace(/^#{1,3}\s/, "").slice(0, 18) || "—";
    }
    if (panel.kind === "clock") {
      const zp = o.local === false && (o.zones || [])[0] ? zoneParts(o.zones[0]) : null;
      out.clock_now = timeIn(zp ? zp.tz : "", { ...o, seconds: false });
      out.clock_label = zp ? (zp.label || zp.tz.split("/").pop().replace(/_/g, " ")) : panel.title;
    }
    if (panel.kind === "timers") {
      const snap = state.timers?.snap || {};
      const p = snap.pomodoro;
      const timer = (snap.timers || []).find((x) => !x.done && x.kind === "timer");
      if (p?.active) { out.timer_left = p.waiting ? "✓" : mmss(pomoLeft(p)); out.timer_label = PHASES()[p.phase] || ""; out.timers_subtitle = t("Помодоро идёт", "Pomodoro running"); }
      else if (timer) { out.timer_left = mmss(timerLeft(timer)); out.timer_label = timer.label || t("Таймер", "Timer"); out.timers_subtitle = t("Идёт таймер", "Timer running"); }
      else { out.timer_left = "—"; out.timer_label = t("Таймеры", "Timers"); out.timers_subtitle = t("Из «Быстрого доступа»", "From Quick Ask"); }
    }
    return out;
  }
  const HIST_KEYS = { cpu: "cpu", ram: "ram", gpu: "gpu", gpu_temp: "gpu_temp", net_down: "net_down_v", net_up: "net_up_v", disk_io: "disk_io_v" };
  const liveSecondsOf = (panel) => (panel.options?.live === "off" ? 0 : +(panel.options?.live || 2) || 2);
  function systemData(panel, data) {
    const o = panel.options || {};
    const live = state.live && liveSecondsOf(panel) ? state.live : null;
    const out = { ...data, ...(live || {}) };
    if (live) {
      const step = Math.max(1, (state.liveMs || 2000) / 1000);
      const n = Math.max(2, Math.round((+o.history || 60) / step));
      Object.keys(HIST_KEYS).forEach((k) => { const h = state.liveHist[k]; if (h && h.length > 1) out[k + "_hist"] = h.slice(-n); });
      if (state.liveProcs && state.liveProcs.procs) Object.assign(out, state.liveProcs);
    }
    if (o.disk_filter) {
      out.disks_list = filterRows(out.disks_list || [], o.disk_filter, (r) => r.label);
      out.disks_rows = filterRows(out.disks_rows || [], o.disk_filter, (r) => r.title);
    }
    if (o.net_filter) {
      out.nets = filterRows(out.nets || [], o.net_filter, (r) => r.title);
      out.nets_bits = filterRows(out.nets_bits || [], o.net_filter, (r) => r.title);
    }
    return out;
  }
  function bodyHtml(panel, mode, size) {
    const data = panel.data || {};
    const ctx = { title: panel.title, trend: "", pulse: false, mode, panel, created: panel.created, wide: mode === "home" && panel.size === "w" };
    const hasData = Object.keys(data).length > 0 || (panel.kind === "system" && !!state.live) || LOCAL_KINDS.includes(panel.kind);

    const head = (sub) => `<div class="mp-title">${ico(KIND_ICON[panel.kind] || "dot")}<div class="mp-title-text"><b>${esc(panel.title)}</b>${sub && sub !== panel.title ? `<small>${esc(sub)}</small>` : ""}</div></div>`;
    if (panel.status === "setup" && panel.kind === "usage") {
      return { html: `${head(t("Нужен аккаунт", "Account needed"))}<div class="mp-setup"><p>${esc(wallText(panel.wall))}</p><div class="mp-setup-row">
        <button type="button" class="mp-btn primary" data-mp-act="edit">${ico("plus")}${t("Добавить аккаунт", "Add an account")}</button></div></div>`, ctx };
    }
    if (panel.status === "setup") {
      return { html: `${head(t("Нужно местоположение", "Location needed"))}
        <div class="mp-setup"><p>${esc(wallText(panel.wall))}</p><div class="mp-setup-row">
          <button type="button" class="mp-btn primary" data-mp-act="locate">${ico("locate")}${t("Определить по IP", "Detect by IP")}</button>
          <button type="button" class="mp-btn" data-mp-act="city">${ico("search")}${t("Указать город", "Set a city")}</button></div></div>`, ctx };
    }
    if (!hasData && (panel.status === "building" || panel.refreshing)) {
      return { html: `${head(tplName(panel.kind))}<div class="mp-skel" aria-hidden="true"><i></i><i></i><i></i></div>${progressHtml(panel)}`, ctx, busy: true };
    }
    if (!hasData) {
      return { html: `${head(tplName(panel.kind) === panel.title ? t("Нет данных", "No data yet") : tplName(panel.kind))}
        <div class="mp-wall" data-kind="error">${ico("alert")}<span>${esc(wallText(panel.wall) || panel.error || t("Не удалось собрать панель.", "Could not build the panel."))}</span>
        ${wallButton(panel.wall)}</div>`, ctx };
    }
    ctx.pulse = state.seen.has(panel.id + ":" + panel.last_refresh) ? false : state.seen.size > 0;
    const live = { ...(panel.kind === "system" ? systemData(panel, data) : data), ...localData(panel) };
    const blocks = (panel.spec?.blocks || []).map((b, i) => { ctx.blockIndex = i; return block(b, live, size, ctx); }).join("");
    const wall = panel.wall && mode !== "sidebar"
      ? `<div class="mp-wall">${ico("alert")}<span>${esc(wallText(panel.wall, panel))}</span>${wallButton(panel.wall)}</div>` : "";
    return { html: blocks + wall, ctx };
  }

  function footHtml(panel, mode) {
    const busy = panel.refreshing || panel.status === "building";
    const sources = (panel.sources || []).slice(0, mode === "sidebar" ? 1 : 2)
      .map((s) => `<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer" draggable="false" title="${esc(s.url)}">${esc(s.name)}</a>`).join('<span class="sep">·</span>');
    if (LOCAL_KINDS.includes(panel.kind) && !busy) {
      if (panel.kind === "timers" && mode !== "sidebar") return `<footer class="mp-foot"><span>${esc(t("Быстрый доступ", "Quick Ask"))}</span></footer>`;
      return "";
    }
    const local = panel.kind === "system" ? `<span>${esc(t("этот компьютер", "this PC"))}</span>` : panel.kind === "proxy" ? `<span>${esc(t("статистика Proxy", "Proxy statistics"))}</span>` : "";
    const when = panel.last_refresh && !["countdown", "system"].includes(panel.source?.type)
      ? `<span data-mp-ago="${panel.last_refresh}" title="${esc(cadenceLabel(panel.cadence))}">${esc(ago(panel.last_refresh))}</span>` : "";
    const stage = busy && panel.progress && Object.keys(panel.data || {}).length ? `<span class="mp-foot-stage">${esc(stageText(panel.progress))}</span>` : "";
    const left = stage || [local || sources, when].filter(Boolean).join('<span class="sep">·</span>');
    if (!left && !busy) return "";
    return `<footer class="mp-foot">${left}<span class="grow"></span>${busy ? `<span class="mp-spin" title="${t("Обновляется", "Refreshing")}"></span>` : ""}</footer>`;
  }

  function cardHtml(panel, mode, opts = {}) {
    const size = opts.size || effectiveSize(panel, mode);
    const { html, ctx } = bodyHtml(panel, mode, size);
    const wx = panel.kind === "weather" ? String(panel.data?.icon || "") : "";
    const trend = ctx.trend === "up" || ctx.trend === "down" ? ctx.trend : "";
    const draggable = opts.static ? "" : ' draggable="true"';
    const busy = (panel.refreshing || panel.status === "building") && Object.keys(panel.data || {}).length > 0;
    const menu = opts.static ? "" : `<button type="button" class="mp-menu-btn" data-mp-menu aria-haspopup="menu" aria-expanded="false" aria-label="${t("Действия панели", "Panel actions")}" title="${t("Действия", "Actions")}">${ico("more")}</button>`;
    const span = mode === "home" && !opts.static ? ` data-span="${spanOf(panel)}"` : "";
    const zsize = opts.zsize ? ` data-zsize="${esc(opts.zsize)}"` : "";
    return `<article class="mp-card${opts.enter ? " mp-enter" : ""}${busy ? " mp-busy" : ""}" data-panel-id="${esc(panel.id)}" data-place="${esc(opts.place || mode)}" data-mode="${mode}" data-size="${size}"${span}${zsize} data-kind="${esc(panel.kind)}" data-status="${esc(panel.status)}"${wx ? ` data-wx="${esc(wx)}"` : ""}${trend ? ` data-trend="${trend}"` : ""}${draggable} tabindex="0" aria-label="${esc(panel.title)}" aria-busy="${panel.refreshing || panel.status === "building" ? "true" : "false"}"${opts.index != null ? ` style="--mp-i:${opts.index}"` : ""}>
      ${busy ? '<i class="mp-busy-bar" aria-hidden="true"></i>' : ""}${menu}${html}${footHtml(panel, mode)}</article>`;
  }

  function miniOf(panel) {
    const data = { ...(panel.kind === "system" ? systemData(panel, panel.data || {}) : (panel.data || {})), ...localData(panel) };
    const spec = panel.spec || {};
    let mini = spec.mini;
    if (!mini) {
      const metric = (spec.blocks || []).find((b) => b.type === "metric") || (spec.blocks || []).flatMap((b) => b.blocks || []).find((b) => b.type === "metric");
      mini = metric ? { value: metric.value, unit: metric.unit, icon: metric.icon } : { value: "" };
    }
    const metric = (spec.blocks || []).find((b) => b.type === "metric");
    return {
      icon: mini.icon ? text(mini.icon, data) : (KIND_ICON[panel.kind] || ""),
      value: text(mini.value, data), unit: text(mini.unit, data),
      label: mini.label ? text(mini.label, data) : panel.title,
      trend: metric ? trendOf(metric, data) : "", delta: metric ? text(metric.delta, data) : "",
    };
  }

  function linesOf(panel, max = 3) {
    const data = { ...(panel.kind === "system" ? systemData(panel, panel.data || {}) : (panel.data || {})), ...localData(panel) };
    const out = [];
    const m = miniOf(panel);
    if (m.value) out.push({ label: m.label || panel.title, value: m.value, unit: m.unit || "", trend: m.trend || "" });
    const walk = (blocks) => {
      for (const b of blocks || []) {
        if (out.length >= max) return;
        if (b.type === "row") { walk(b.blocks); continue; }
        if (b.type === "metric") {
          const value = text(b.value, data);
          if (value && !out.some((l) => l.value === value)) out.push({ label: b.label ? text(b.label, data) : panel.title, value, unit: b.unit ? text(b.unit, data) : "", trend: trendOf(b, data) });
        } else if (b.type === "stats") {
          const items = raw(b.items, data);
          for (const it of Array.isArray(items) ? items : []) {
            if (out.length >= max) break;
            const value = text(it.value, data);
            if (value) out.push({ label: text(it.label, data), value, unit: it.unit ? text(it.unit, data) : "", trend: "" });
          }
        } else if (b.type === "list") {
          const items = raw(b.items, data);
          for (const it of Array.isArray(items) ? items : []) {
            if (out.length >= max) break;
            const row = typeof it === "object" && it ? it : { title: it };
            const title = text(row.title, data);
            if (title) out.push({ label: row.value ? title : "", value: row.value ? text(row.value, data) : title, unit: "", trend: "" });
          }
        } else if (b.type === "countdown" || b.type === "progress" || b.type === "text") {
          const value = text(b.value || b.text || b.label, data);
          if (value && !out.some((l) => l.value === value)) out.push({ label: b.label && b.value ? text(b.label, data) : "", value, unit: "", trend: "" });
        }
      }
    };
    walk((panel.spec || {}).blocks);
    return out.slice(0, max);
  }

  function chipHtml(panel, opts = {}) {
    const m = miniOf(panel);
    const hasData = Object.keys(panel.data || {}).length > 0;

    const failed = !hasData && panel.status === "failed" && !!panel.wall;
    const stateName = panel.status === "setup" ? "setup" : failed ? "wall" : !hasData ? "building" : panel.wall ? "wall" : "live";

    const value = stateName === "setup" ? t("Где вы?", "Where?") : stateName === "building" ? "…"
      : (hasData && (m.value || (opts.place && opts.place !== "appbar" ? linesOf(panel, 1)[0]?.value : ""))) || "—";
    const tip = `${panel.title}${hasData && m.value ? ` — ${m.value}${m.unit || ""}` : ""}${m.delta ? ` (${m.delta})` : ""}${panel.wall && panel.status !== "setup" ? ` · ${wallText(panel.wall, panel)}` : ""}`;
    return `<button type="button" class="mp-chip${opts.enter ? " mp-enter" : ""}" data-panel-id="${esc(panel.id)}" data-place="${esc(opts.place || "appbar")}" data-state="${stateName}"${m.trend ? ` data-trend="${esc(m.trend)}"` : ""} draggable="true" data-no-drag aria-haspopup="dialog" aria-expanded="false" title="${esc(tip)}"${opts.index != null ? ` style="--mp-i:${opts.index}"` : ""}>
      ${ico(stateName === "setup" ? "locate" : (m.icon || KIND_ICON[panel.kind] || "dot"))}<b class="mp-chip-value">${esc(value)}</b>${m.unit && hasData && (stateName === "live" || stateName === "wall") ? `<span class="mp-chip-unit">${esc(m.unit)}</span>` : ""}${m.label ? `<span class="mp-chip-label">${esc(m.label)}</span>` : ""}</button>`;
  }

  function ensureMounts() {
    const wrap = $q("#chat-view-wrap");
    if (wrap && !$q("#panels-home")) {
      const home = document.createElement("section");
      home.id = "panels-home";
      home.className = "panels-home";
      home.setAttribute("aria-label", t("Панели", "Panels"));
      const composer = $q(".composer-wrap", wrap);
      if (composer && composer.parentElement === wrap) composer.after(home); else wrap.appendChild(home);
    }
    const nav = $q(".side-scroll .side-nav");
    if (nav && !$q("#group-panels")) {
      const group = document.createElement("div");
      group.className = "side-group";
      group.id = "group-panels";
      group.dataset.section = "panels";
      group.innerHTML = `<div class="chat-list-heading"><span class="side-label" data-mp-i18n="panels">${t("Панели", "Panels")}</span><button type="button" id="btn-panel-new" title="${t("Новая панель", "New panel")}" aria-label="${t("Новая панель", "New panel")}">${ico("plus")}</button></div>
        <div class="mp-side-list mp-drop-zone" data-mp-zone="sidebar"><div class="mp-side-empty">${t("Перетащите панель сюда", "Drop a panel here")}</div></div>`;
      nav.after(group);
    }
    const focusBtn = $q(".topbar #btn-focus");
    if (focusBtn && !$q("#panels-appbar")) {
      const bar = document.createElement("div");
      bar.id = "panels-appbar";
      bar.className = "panels-appbar mp-drop-zone";
      bar.dataset.mpZone = "appbar";
      bar.setAttribute("data-no-drag", "");
      bar.setAttribute("role", "toolbar");
      bar.setAttribute("aria-label", t("Мини-панели", "Mini panels"));
      focusBtn.before(bar);
    }
    LAY()?.ensureMounts();
  }

  const placed = (place) => state.panels.filter((p) => (p.placements || []).includes(place))
    .sort((a, b) => ((a.order || {})[place] ?? 0) - ((b.order || {})[place] ?? 0));

  function captureRects() {
    const rects = new Map();
    $$q("[data-panel-id][data-place]").forEach((el) => {
      if (el.closest(".mp-pop, .mp-modal, #messages")) return;
      rects.set(el.dataset.place + ":" + el.dataset.panelId, el.getBoundingClientRect());
    });
    return rects;
  }
  function playFlip(before) {
    if (motionOff() || !before.size) return;
    $$q("[data-panel-id][data-place]").forEach((el) => {
      const old = before.get(el.dataset.place + ":" + el.dataset.panelId);
      if (!old || el.classList.contains("mp-enter")) return;
      const now = el.getBoundingClientRect();
      const dx = old.left - now.left, dy = old.top - now.top;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
      el.animate([{ translate: `${dx}px ${dy}px` }, { translate: "0 0" }], { duration: 260, easing: "cubic-bezier(.16,1,.3,1)" });
    });
  }

  function keepFocus(fn) {
    const a = document.activeElement;
    let restore = null;
    const card = a?.closest?.("[data-panel-id][data-place]");
    if (card && a !== card) {
      const sel = a.dataset?.mpKeep ? `[data-mp-keep="${CSS.escape(a.dataset.mpKeep)}"]`
        : a.dataset?.todoItem ? `[data-todo-item="${CSS.escape(a.dataset.todoItem)}"]`
          : a.matches?.("[data-note-edit]") ? "[data-note-edit]" : "";
      if (sel) {
        let range = null;
        try { range = [a.selectionStart, a.selectionEnd]; } catch (e) { range = null; }
        restore = { sel, pid: card.dataset.panelId, place: card.dataset.place, value: "value" in a ? a.value : null, range };
      }
    }
    fn();
    if (!restore) return;
    const el = document.querySelector(`[data-panel-id="${CSS.escape(restore.pid)}"][data-place="${CSS.escape(restore.place)}"] ${restore.sel}`);
    if (!el || el === document.activeElement) return;
    if (restore.value != null && "value" in el && el.value !== restore.value) el.value = restore.value;
    el.focus({ preventScroll: true });
    if (restore.range && restore.range[0] != null && el.setSelectionRange) { try { el.setSelectionRange(restore.range[0], restore.range[1]); } catch (e) {  } }
  }

  function rerender(pred) {
    keepFocus(() => {
      $$q(".mp-card[data-panel-id]").forEach((el) => {
        if (el.closest(".mp-modal") && !el.closest(".mp-preview-live")) return;
        const panel = byId(el.dataset.panelId);
        if (!panel || panel.kind === "module" || (pred && !pred(panel))) return;
        const opts = { size: el.dataset.size, place: el.dataset.place, static: !el.hasAttribute("draggable") };
        const fresh = document.createElement("div");
        fresh.innerHTML = cardHtml(panel, el.dataset.mode, opts);
        const card = fresh.firstElementChild;
        if (!card) return;
        const expanded = el.querySelector("[data-mp-menu]")?.getAttribute("aria-expanded");
        if (el.innerHTML !== card.innerHTML) el.innerHTML = card.innerHTML;
        ["data-status", "data-trend", "data-wx", "aria-busy"].forEach((attr) => {
          if (card.hasAttribute(attr)) el.setAttribute(attr, card.getAttribute(attr)); else el.removeAttribute(attr);
        });
        if (expanded === "true") el.querySelector("[data-mp-menu]")?.setAttribute("aria-expanded", "true");
      });
      $$q(".mp-chip[data-panel-id][data-place]").forEach((chip) => {
        const panel = byId(chip.dataset.panelId);
        if (!panel || !pred(panel)) return;
        const m = miniOf(panel);
        const value = chip.querySelector(".mp-chip-value");
        if (value && m.value && value.textContent !== m.value) value.textContent = m.value;
      });
    });
    layoutGrid();
  }

  const ROW_UNIT = 2;
  function layoutGrid() {
    const grid = $q("#panels-home .mp-grid");
    if (!grid) return;
    if (grid.offsetParent === null) { observeGrid(grid); return; }
    const gap = parseFloat(getComputedStyle(grid).columnGap) || 10;
    grid.style.setProperty("--mp-row-gap", gap + "px");
    $$q(":scope > *", grid).forEach((el) => {
      const h = el.getBoundingClientRect().height;
      const span = Math.max(1, Math.ceil((h + gap) / ROW_UNIT));
      if (el.style.gridRowEnd !== `span ${span}`) el.style.gridRowEnd = `span ${span}`;
    });
    grid.dataset.masonry = "on";
    observeGrid(grid);
    if (!state.liveTimer || !state.timersTimer) syncLive();
  }
  let gridRaf = 0;
  function queueLayout() { cancelAnimationFrame(gridRaf); gridRaf = requestAnimationFrame(layoutGrid); }
  function observeGrid(grid) {
    if (typeof ResizeObserver !== "function") return;
    if (!state.gridRO) state.gridRO = new ResizeObserver(queueLayout);
    if (state.gridObserved !== grid) { state.gridRO.disconnect(); state.gridObserved = grid; state.gridRO.observe(grid); }
    $$q(":scope > *", grid).forEach((el) => { if (!el.dataset.mpObserved) { el.dataset.mpObserved = "1"; state.gridRO.observe(el); } });
  }

  function render() {
    ensureMounts();
    const before = captureRects();
    window.MemuModInt?.park();
    keepFocus(() => { renderHome(); renderSidebar(); renderAppbar(); LAY()?.renderZones(); });
    window.MemuModInt?.mount();
    layoutGrid();
    playFlip(before);
    refreshInline();
    renderProgressModal();
    if ($q("#pane-panels .mp-settings")) renderSettings();
    subscribers.forEach((fn) => { try { fn(state); } catch (e) { console.warn("[MemuPanels]", e); } });
    state.panels.forEach((p) => state.seen.add(p.id + ":" + p.last_refresh));
  }

  function isNew(place, id) {
    const key = "r:" + place + ":" + id;
    if (state.seen.has(key)) return false;
    state.seen.add(key);
    return state.loaded;
  }

  function renderHome() {
    const home = $q("#panels-home");
    if (!home) return;
    const list = placed("home");
    if (state.settings.enabled === false && !list.length) { home.hidden = true; return; }
    home.hidden = false;
    home.innerHTML = `<div class="panels-home-head"><span class="mp-home-label">${LAY()?.editing() ? `${ico("zhome")}${esc(placeName("home"))}` : t("Панели", "Panels")}</span><span class="grow"></span>
        <button type="button" class="mp-head-btn" data-mp-act="arrange" title="${t("Изменить расположение панелей — перетаскивание, размеры, новые места", "Edit the panel layout — drag, sizes, new places")}">${ico("arrange")}<span class="mp-head-label">${t("Расположение", "Layout")}</span></button>
        <button type="button" class="mp-head-btn" data-mp-act="gallery" title="${t("Добавить панель", "Add a panel")}">${ico("plus")}${t("Панель", "Panel")}</button></div>
      <div class="mp-grid mp-drop-zone" data-mp-zone="home">${list.map((p, i) => cardHtml(p, "home", { enter: isNew("home", p.id), index: i })).join("")}
      ${list.length < 3 ? `<button type="button" class="mp-add-tile" data-mp-act="gallery">${ico("plus")}<span>${t("Добавить панель", "Add a panel")}</span></button>` : ""}</div>`;
  }

  function renderSidebar() {
    const group = $q("#group-panels");
    if (!group) return;
    const list = placed("sidebar");
    group.hidden = !list.length;
    const box = $q(".mp-side-list", group);
    const chips = LAY()?.viewOf("sidebar") === "chips";
    box.dataset.view = chips ? "chips" : "cards";
    box.innerHTML = list.map((p, i) => chips ? chipHtml(p, { enter: isNew("sidebar", p.id), index: i, place: "sidebar" })
      : cardHtml(p, "sidebar", { enter: isNew("sidebar", p.id), index: i })).join("")
      + `<div class="mp-side-empty">${t("Перетащите панель сюда", "Drop a panel here")}</div>`;
    const label = $q("[data-mp-i18n=panels]", group);
    if (label) label.textContent = t("Панели", "Panels");
  }

  function renderAppbar() {
    const bar = $q("#panels-appbar");
    if (!bar) return;
    const list = placed("appbar");
    const cap = Math.max(1, Math.min(8, +state.settings.appbar_max || 4));
    bar.innerHTML = list.map((p, i) => chipHtml(p, { enter: isNew("appbar", p.id), index: i })).join("");
    bar.dataset.cap = String(cap);
    fitAppbar();
  }

  function fitAppbar() {
    const bar = $q("#panels-appbar");
    if (!bar) return;
    $q(".mp-more", bar)?.remove();
    const chips = $$q(".mp-chip[data-panel-id]", bar);
    chips.forEach((c) => { c.hidden = false; });
    bar.classList.remove("mp-narrow");
    if (!chips.length) return;
    const cap = +bar.dataset.cap || 4;

    const fits = () => {
      const shown = Array.from(bar.children).filter((c) => !c.hidden);
      const gap = parseFloat(getComputedStyle(bar).columnGap) || 0;
      const need = shown.reduce((sum, c) => sum + c.getBoundingClientRect().width, 0) + gap * Math.max(0, shown.length - 1);
      return need <= bar.clientWidth - 4 + 0.5;
    };
    if (!fits()) bar.classList.add("mp-narrow");
    let hidden = [];
    chips.forEach((c, i) => { if (i >= cap) { c.hidden = true; hidden.push(c); } });
    const more = document.createElement("button");
    more.type = "button"; more.className = "mp-chip mp-more"; more.setAttribute("data-no-drag", "");
    more.dataset.mpAct = "more";
    const update = () => { more.textContent = "+" + hidden.length; more.title = t(`Ещё ${hidden.length}`, `${hidden.length} more`); };
    if (hidden.length) { update(); bar.appendChild(more); }
    for (let i = chips.length - 1; i >= 0 && !fits(); i -= 1) {
      if (chips[i].hidden) continue;
      if (chips.filter((c) => !c.hidden).length <= 1) break;
      chips[i].hidden = true; hidden.unshift(chips[i]);
      update();
      if (!more.isConnected) bar.appendChild(more);
    }
  }

  async function load(force = false) {
    if (state.loading && !force) return state.loading;
    state.loading = (async () => {
      try {
        const data = await call("/api/pg/panels");
        const json = JSON.stringify([data.panels, data.dismissed, data.settings]);
        state.templates = data.templates || [];
        const changed = json !== state.lastJson;
        state.lastJson = json;
        state.panels = data.panels || [];
        state.dismissed = data.dismissed || [];
        state.settings = data.settings || {};
        if (data.limits?.floors) state.floors = data.limits.floors;
        if (data.limits?.floor_reasons) state.floorReasons = data.limits.floor_reasons;
        if (data.option_schema) state.schema = data.option_schema;
        if (Array.isArray(data.limits?.presets)) state.presets = data.limits.presets;
        const first = !state.loaded;
        state.loaded = true;
        if (changed || first) render();
        syncLang();
      } catch (error) {
        if (!state.loaded) ensureMounts();
      } finally {
        state.loading = null;
        schedule();
      }
    })();
    return state.loading;
  }
  function busy() { return state.panels.some((p) => p.status === "building" || p.refreshing); }
  function schedule() {
    clearTimeout(state.timer);
    const building = busy();
    const fast = building || Date.now() < state.fastUntil;

    const shortest = Math.min(...state.panels.filter((p) => p.cadence?.kind === "interval").map((p) => +p.cadence.minutes || 60), 60);
    const idle = shortest <= 5 ? 20000 : 45000;

    if (state.paused) { clearInterval(state.liveTimer); state.liveTimer = 0; clearInterval(state.timersTimer); state.timersTimer = 0; return; }
    state.timer = setTimeout(() => { if (!document.hidden || fast) load(); else schedule(); }, building ? 1500 : fast ? 3000 : idle);
    syncLive();
  }

  const visibleCards = (kind) => $$q(`[data-panel-id][data-kind="${kind}"]`).filter((el) => el.getClientRects().length > 0 && !(el.closest(".mp-modal") && !el.closest(".mp-preview-live")))
    .map((el) => byId(el.dataset.panelId)).filter(Boolean);
  const visibleChips = (kind) => $$q(".mp-chip[data-panel-id][data-place]:not([hidden])").filter((el) => el.getClientRects().length > 0).map((el) => byId(el.dataset.panelId)).filter((p) => p?.kind === kind);
  function liveVisible() {
    if (document.hidden) return false;
    return visibleCards("system").length > 0 || visibleChips("system").length > 0;
  }

  function liveWanted() {
    if (document.hidden) return 0;
    const shown = [...visibleCards("system"), ...visibleChips("system")];
    const secs = shown.map(liveSecondsOf).filter((x) => x > 0);
    return secs.length ? Math.min(...secs) * 1000 : 0;
  }
  function syncLive() {
    if (state.paused) { clearInterval(state.liveTimer); state.liveTimer = 0; clearInterval(state.timersTimer); state.timersTimer = 0; return; }
    const ms = state.panels.some((p) => p.kind === "system") ? liveWanted() : 0;
    if (ms && (!state.liveTimer || state.liveMs !== ms)) {
      clearInterval(state.liveTimer);
      state.liveMs = ms;
      if (!state.liveTimer) tickLive();
      state.liveTimer = setInterval(tickLive, ms);
    } else if (!ms && state.liveTimer) { clearInterval(state.liveTimer); state.liveTimer = 0; }
    const timersShown = !document.hidden && (visibleCards("timers").length > 0 || visibleChips("timers").length > 0);
    if (timersShown && !state.timersTimer) { loadTimers().then(() => rerender((p) => p.kind === "timers")); state.timersTimer = setInterval(async () => { if (document.hidden || state.drag) return; await loadTimers(); rerender((p) => p.kind === "timers"); }, 3000); }
    else if (!timersShown && state.timersTimer) { clearInterval(state.timersTimer); state.timersTimer = 0; }
  }
  async function tickLive() {
    if (state.drag || document.hidden || state.liveBusy) return;
    state.liveBusy = true;
    const procs = [...visibleCards("system")].filter((p) => (p.options?.metrics || []).includes("procs"));
    const want = procs.length ? Math.max(...procs.map((p) => +p.options?.procs_count || 5)) : 0;
    try {
      const out = await call(`/api/pg/panels-system?lang=${en() ? "en" : "ru"}${want ? `&procs=${want}&sort=${procs[0].options?.procs_sort === "mem" ? "mem" : "cpu"}` : ""}`);
      state.live = out.values || null;
      if (state.live) {
        Object.entries(HIST_KEYS).forEach(([name, key]) => {
          const v = Number(state.live[key]);
          if (state.live[key] == null || !Number.isFinite(v)) return;
          const h = (state.liveHist[name] = state.liveHist[name] || []);
          h.push(v);
          if (h.length > 900) h.splice(0, h.length - 900);
        });
        state.liveProcs = want ? { procs: state.live.procs || [], procs_note: state.live.procs_note || "" } : null;
      }
      rerender((p) => p.kind === "system");
    } catch (error) {  }
    finally { state.liveBusy = false; }
    if (!liveVisible()) syncLive();
  }
  function soon() { state.fastUntil = Date.now() + 90000; schedule(); }

  function syncLang() {
    const lang = en() ? "en" : "ru";
    const locale = navigator.language || "";
    if (state.langSynced === lang + locale) return;
    state.langSynced = lang + locale;
    if (state.settings.lang !== lang || state.settings.locale !== locale) {
      call("/api/pg/panels-settings", { method: "POST", body: { lang, locale } }).then(() => load(true)).catch(() => {});
    }
  }

  async function act(promise, okText = "") {
    try {
      const out = await promise;
      if (okText) notify(okText);
      await load(true);
      soon();
      return out;
    } catch (error) {
      notify(error.message || String(error), "err");
      await load(true);
      return null;
    }
  }
  function patch(id, body) { return act(call(`/api/pg/panels/${id}`, { method: "PATCH", body })); }
  async function refreshNow(id) {
    const panel = byId(id);
    if (panel) { panel.refreshing = true; render(); }
    soon();
    const out = await act(call(`/api/pg/panels/${id}/refresh`, { method: "POST", body: { wait: 25 } }));
    if (out?.panel?.wall) notify(wallText(out.panel.wall, out.panel), "err");
    flash(id);
  }
  async function remove(id) {
    const panel = byId(id);
    await act(call(`/api/pg/panels/${id}`, { method: "DELETE" }));
    if (typeof toast === "function" && panel) {
      toast(t(`Панель «${panel.title}» убрана`, `Panel “${panel.title}” removed`), "info",
        { label: t("Вернуть", "Undo"), run: () => act(call(`/api/pg/panels/${id}/revive`, { method: "POST" })) });
    }
  }
  function setPlaces(id, places) {
    const panel = byId(id);
    if (panel) { panel.placements = places; render(); }
    return patch(id, { placements: places });
  }
  function togglePlace(id, place) {
    const panel = byId(id);
    if (!panel) return;
    const set = new Set(panel.placements || []);
    if (set.has(place)) set.delete(place); else set.add(place);
    return setPlaces(id, PLACES.filter((p) => set.has(p)));
  }
  function flash(id) {
    if (motionOff()) return;
    requestAnimationFrame(() => $$q(`[data-panel-id="${CSS.escape(id)}"]`).forEach((el) => {
      el.classList.remove("mp-flash"); void el.offsetWidth; el.classList.add("mp-flash");
    }));
  }
  function askInChat(prompt) {
    if (!prompt) return;
    if (EMBED && window.MEMU_PANELS_HOST && window.MEMU_PANELS_HOST.ask) { window.MEMU_PANELS_HOST.ask(prompt); return; }
    if (EMBED && window.MemuQuick) { window.MemuQuick.send(prompt); return; }
    try {
      if (typeof newChat === "function") newChat();
      const input = $q("#input");
      if (!input) return;
      input.value = prompt;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      if (typeof send === "function") send(); else input.focus();
    } catch (error) { notify(error.message, "err"); }
  }

  function closeMenu() {
    if (state.menu?.handle) { const m = state.menu; state.menu = null; m.handle.close("api"); return; }
    if (state.menu) {
      state.menu.el.remove();
      state.menu.anchor?.setAttribute("aria-expanded", "false");
      state.menu = null;
    }
  }
  function position(el, anchorRect, { align = "end", gap = 6 } = {}) {
    const vw = innerWidth, vh = innerHeight;
    const w = el.offsetWidth, h = el.offsetHeight;
    let left = align === "end" ? anchorRect.right - w : anchorRect.left;
    let top = anchorRect.bottom + gap;
    let origin = align === "end" ? "top right" : "top left";
    if (top + h > vh - 8) { top = Math.max(8, anchorRect.top - h - gap); origin = origin.replace("top", "bottom"); }
    left = Math.max(8, Math.min(left, vw - w - 8));
    el.style.left = left + "px"; el.style.top = top + "px";
    el.style.setProperty("--mp-origin", origin);
  }

  function ctxItems(panel, card) {
    const places = new Set(panel.placements || []);
    const sel = String(window.getSelection?.() || "").trim();
    const selInCard = sel && card && window.getSelection().anchorNode && card.contains(window.getSelection().anchorNode);
    return [
      selInCard ? { id: "copy", label: t("Копировать выделенное", "Copy selection"), icon: "copy", hint: "Ctrl+C" } : null,
      selInCard ? { type: "sep" } : null,
      LOCAL_KINDS.includes(panel.kind) ? null : { id: "refresh", label: t("Обновить сейчас", "Refresh now"), icon: "refresh" },
      { id: "edit", label: t("Настроить…", "Customize…"), icon: "sliders" },
      panel.source?.type === "feed" ? { id: "feed-reset", label: t("Собрать заново, с нуля", "Rebuild from scratch"), icon: "reset", title: t("Лента забудет, что уже показывала, и соберётся заново", "The feed forgets what it showed and rebuilds") } : null,
      { type: "sep" }, { type: "label", label: t("Показывать", "Show in") },

      ...PLACES.filter((p) => places.has(p) || ["home", "sidebar", "appbar"].includes(p))
        .map((p) => ({ id: "place:" + p, label: placeName(p), icon: placeIcon(p), checked: places.has(p), keep: true })),
      LAY() ? { id: "places", label: t("Другие места…", "More places…"), icon: "layout", title: t("Карта мест окна: над приветствием, над полем ввода, колонки, низ боковой панели", "A map of the window: above the greeting, above the message box, columns, sidebar bottom") } : null,
      [...places].some((p) => LAY()?.sized(p) ?? p === "home") ? { type: "seg", id: "size", label: t("Размер", "Size"), icon: "size", keep: true,
        options: ["s", "m", "l", "w"].map((k) => ({ id: k, label: k.toUpperCase(), title: SIZE_NAMES()[k] + " — " + SIZE_HINTS()[k], pressed: panel.size === k })) } : null,
      { type: "sep" },
      LAY() && !LAY().editing() ? { id: "arrange", label: t("Изменить расположение", "Edit layout"), icon: "move" } : null,
      { id: "remove", label: t("Убрать панель", "Remove panel"), icon: "trash", danger: true, hint: "Del" },
    ];
  }
  function openCtxMenu(id, anchor, point, keyboard) {
    const panel = byId(id);
    if (!panel) return;
    const card = anchor?.closest?.("[data-panel-id]") || anchor;
    const menuBtn = card?.querySelector?.("[data-mp-menu]") || null;
    let handle = null;
    const opts = {
      items: ctxItems(panel, card), owner: menuBtn || card, returnFocus: card?.isConnected ? card : undefined,
      keyboard: !!keyboard, label: t("Действия панели", "Panel actions"), className: "mp-ctx",
      onPick: (m) => {
        if (m === "copy") { try { navigator.clipboard.writeText(String(window.getSelection() || "")); } catch (e) { document.execCommand?.("copy"); } return; }
        if (m === "refresh") { refreshNow(id); return; }
        if (m === "edit") { openEditor(id); return; }
        if (m === "feed-reset") { const p = byId(id); if (p) { p.refreshing = true; render(); } act(call(`/api/pg/panels/${id}/feed-reset`, { method: "POST" })); return; }
        if (m === "places") { LAY()?.openPlaces(id, card); return; }
        if (m === "arrange") { LAY()?.enter(id); return; }
        if (m.startsWith("place:")) {
          LAY()?.remember();
          togglePlace(id, m.slice(6));
          const p = byId(id);
          if (handle && p) handle.update(ctxItems(p, card));
          return false;
        }
        if (["s", "m", "l", "w"].includes(m)) {
          LAY()?.remember();
          const p = byId(id); if (p) { p.size = m; render(); }
          patch(id, { size: m });
          return false;
        }
        if (m === "remove") { remove(id); }
      },
      onClose: (reason) => {
        if (state.menu && state.menu.handle === handle) state.menu = null;
        state.menuClosed = { id, reason, at: performance.now() };
      },
    };
    if (point) { opts.x = point.x; opts.y = point.y; } else opts.anchor = anchor;
    handle = window.MemuCtxMenu.open(opts);
    if (handle) state.menu = { el: handle.el, anchor: menuBtn || card, id, handle };
  }
  function openMenu(id, anchor, point = null, keyboard = false) {
    closeMenu(); closePop();
    if (window.MemuCtxMenu && !EMBED) { openCtxMenu(id, anchor, point, keyboard); return; }
    const panel = byId(id);
    if (!panel) return;
    const places = new Set(panel.placements || []);
    const el = document.createElement("div");
    el.className = "mp-menu";
    el.setAttribute("role", "menu");
    el.innerHTML = `
      ${LOCAL_KINDS.includes(panel.kind) ? "" : `<button class="mp-mi" role="menuitem" data-m="refresh">${ico("refresh")}${t("Обновить сейчас", "Refresh now")}</button>`}
      <button class="mp-mi" role="menuitem" data-m="edit">${ico("sliders")}${t("Настроить…", "Customize…")}</button>
      ${panel.source?.type === "feed" ? `<button class="mp-mi" role="menuitem" data-m="feed-reset" title="${esc(t("Лента забудет, что уже показывала, и соберётся заново", "The feed forgets what it showed and rebuilds"))}">${ico("refresh")}${t("Собрать заново, с нуля", "Rebuild from scratch")}</button>` : ""}
      <hr><div class="mp-mlabel">${t("Показывать", "Show in")}</div>
      ${PLACES.filter((p) => places.has(p) || ["home", "sidebar", "appbar"].includes(p)).map((p) => `<button class="mp-mi" role="menuitemcheckbox" aria-checked="${places.has(p)}" data-m="place" data-place="${p}">${ico(placeIcon(p))}${placeName(p)}${places.has(p) ? ico("check", "check") : ""}</button>`).join("")}
      ${places.has("home") ? `<div class="mp-mlabel">${t("Размер на главном экране", "Size on the home screen")}</div>
      <div class="mp-seg" role="group">${["s", "m", "l", "w"].map((s) => `<button type="button" data-m="size" data-size="${s}" aria-pressed="${panel.size === s}" title="${esc(SIZE_NAMES()[s] + " — " + SIZE_HINTS()[s])}">${{ s: "S", m: "M", l: "L", w: "W" }[s]}</button>`).join("")}</div>` : ""}
      <hr><button class="mp-mi danger" role="menuitem" data-m="remove">${ico("trash")}${t("Убрать панель", "Remove panel")}</button>`;
    document.body.appendChild(el);
    const rect = point ? new DOMRect(point.x, point.y, 0, 0) : anchor.getBoundingClientRect();
    position(el, rect, { align: point ? "start" : "end", gap: point ? 2 : 6 });
    anchor?.setAttribute?.("aria-expanded", "true");
    state.menu = { el, anchor, id };
    el.addEventListener("click", (event) => {
      const item = event.target.closest("[data-m]");
      if (!item) return;
      const m = item.dataset.m;
      if (m === "refresh") { closeMenu(); refreshNow(id); }
      else if (m === "edit") { closeMenu(); openEditor(id); }
      else if (m === "feed-reset") { closeMenu(); const p = byId(id); if (p) { p.refreshing = true; render(); } act(call(`/api/pg/panels/${id}/feed-reset`, { method: "POST" })); }
      else if (m === "place") { togglePlace(id, item.dataset.place); closeMenu(); }
      else if (m === "size") {
        $$q("[data-m=size]", el).forEach((b) => b.setAttribute("aria-pressed", String(b === item)));
        const p = byId(id); if (p) { p.size = item.dataset.size; render(); }
        patch(id, { size: item.dataset.size });
      } else if (m === "remove") { closeMenu(); remove(id); }
    });
    el.addEventListener("keydown", (event) => {
      const items = $$q("button", el);
      const index = items.indexOf(document.activeElement);
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        items[(index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus();
      } else if (event.key === "Escape") { closeMenu(); anchor?.focus?.(); }
    });
    requestAnimationFrame(() => $q("button", el)?.focus({ preventScroll: true }));
  }

  function closePop() {
    if (state.pop) {
      state.pop.el.remove();
      state.pop.anchor?.setAttribute("aria-expanded", "false");
      state.pop = null;
    }
  }
  function openPop(id, anchor) {
    if (state.pop?.id === id) { closePop(); return; }
    closePop(); closeMenu();
    const panel = byId(id);
    if (!panel) return;
    const el = document.createElement("div");
    el.className = "mp-pop";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-label", panel.title);
    el.innerHTML = cardHtml(panel, "popover", { size: "l", place: "popover" });
    document.body.appendChild(el);
    position(el, anchor.getBoundingClientRect(), { align: "start" });
    anchor.setAttribute("aria-expanded", "true");
    state.pop = { el, anchor, id };
  }
  function openMore(anchor) {
    closePop(); closeMenu();
    const hidden = $$q("#panels-appbar .mp-chip[data-panel-id][hidden]");
    const el = document.createElement("div");
    el.className = "mp-pop";
    el.innerHTML = `<div class="mp-pop-list">${hidden.map((chip) => chipHtml(byId(chip.dataset.panelId))).join("")}</div>`;
    document.body.appendChild(el);
    el.style.width = "auto";
    position(el, anchor.getBoundingClientRect());
    state.pop = { el, anchor, id: "__more" };
  }

  function listPopHtml(panel, index) {
    const b = (panel.spec?.blocks || [])[index] || (panel.spec?.blocks || []).find((x) => x.type === "list");
    const items = b ? raw(b.items, panel.data || {}) : [];
    const rows = (Array.isArray(items) ? items : []).map((it) => {
      const row = typeof it === "object" && it ? it : { title: it };
      const url = /^https?:\/\//i.test(String(row.url || "")) ? row.url : "";
      const links = (Array.isArray(row.links) ? row.links : []).filter((l) => /^https?:\/\//i.test(String(l?.url || ""))).slice(0, 4);
      return `<li class="mp-detail-row">
        ${url ? `<a class="mp-detail-title" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(text(row.title, panel.data))}${ico("external")}</a>` : `<b class="mp-detail-title">${esc(text(row.title, panel.data))}</b>`}
        <div class="mp-detail-meta">${itemMeta(row, panel.data) || esc(text(row.subtitle, panel.data))}</div>
        ${row.summary ? `<p class="mp-detail-sum">${esc(text(row.summary, panel.data))}</p>` : ""}
        ${row.why ? `<p class="mp-detail-why">${ico("info")}<span>${esc(t("Почему здесь: ", "Why it's here: "))}${esc(text(row.why, panel.data))}</span></p>` : ""}
        ${links.length ? `<div class="mp-detail-links">${links.map((l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.name || l.url)}</a>`).join("")}</div>` : ""}</li>`;
    }).join("");
    const stats = panel.data?.stats;
    const foot = stats ? `<div class="mp-detail-foot">${esc(t(`Просмотрено источников: ${stats.sources_ok}${stats.sources_failed ? ` (не ответили: ${stats.sources_failed})` : ""} · кандидатов: ${stats.candidates} · историй: ${stats.stories}`,
      `Sources read: ${stats.sources_ok}${stats.sources_failed ? ` (${stats.sources_failed} failed)` : ""} · candidates: ${stats.candidates} · stories: ${stats.stories}`))}${panel.data?.rated_by_model ? "" : `<br>${esc(t("Оценка без модели: MemuAI Proxy или модель недоступны — только эвристика источников.", "Ranked without a model: MemuAI Proxy or a model is unavailable — source heuristics only."))}`}</div>` : "";
    return `<div class="mp-detail" role="dialog" aria-label="${esc(panel.title)}"><div class="mp-detail-head">${ico(KIND_ICON[panel.kind] || "list")}<b>${esc(panel.title)}</b>
      <button type="button" class="mp-x" data-mp-pop-close aria-label="${t("Закрыть", "Close")}">${ico("close")}</button></div>
      <ol class="mp-detail-list">${rows || `<li class="mp-detail-row">${t("Пока пусто", "Nothing yet")}</li>`}</ol>${foot}</div>`;
  }
  function openListPop(id, index, anchor) {
    closePop(); closeMenu();
    const panel = byId(id);
    if (!panel) return;
    const el = document.createElement("div");
    el.className = "mp-pop mp-pop-detail";
    el.innerHTML = listPopHtml(panel, index);
    document.body.appendChild(el);
    position(el, anchor.getBoundingClientRect(), { align: "start" });
    state.pop = { el, anchor, id: "__list:" + id, list: index };
    requestAnimationFrame(() => $q(".mp-detail-title, [data-mp-pop-close]", el)?.focus({ preventScroll: true }));
  }
  function refreshPop() {
    if (state.pop?.id?.startsWith("__list:")) {
      const panel = byId(state.pop.id.slice(7));
      if (panel) state.pop.el.innerHTML = listPopHtml(panel, state.pop.list);
      return;
    }
    if (!state.pop || state.pop.id === "__more") return;
    const panel = byId(state.pop.id);
    if (!panel || !(panel.placements || []).length) { closePop(); return; }
    state.pop.el.innerHTML = cardHtml(panel, "popover", { size: "l", place: "popover" });
  }

  function closeModal() {
    if (!state.modal) return;
    state.modal.remove();
    state.modal = null;
    document.removeEventListener("keydown", modalKeys, true);
  }
  function modalKeys(event) {
    if (event.key === "Escape" && state.modal) { event.stopPropagation(); event.preventDefault(); closeModal(); }
  }
  function openModal(html, cls = "") {
    closeModal(); closeMenu(); closePop();
    const back = document.createElement("div");
    back.className = "mp-modal-back";
    back.innerHTML = `<div class="mp-modal ${cls}" role="dialog" aria-modal="true">${html}</div>`;
    back.addEventListener("mousedown", (event) => { if (event.target === back) closeModal(); });
    document.body.appendChild(back);
    state.modal = back;
    document.addEventListener("keydown", modalKeys, true);
    requestAnimationFrame(() => $q("input, textarea, select, .mp-tpl, button.primary", back)?.focus({ preventScroll: true }));
    return back;
  }
  const head = (title, sub = "", back = false) => `<div class="mp-modal-head">${back ? `<button type="button" class="mp-x" data-mp-back aria-label="${t("Назад", "Back")}">${ico("back")}</button>` : ""}
    <div style="flex:1;min-width:0"><h3>${esc(title)}</h3>${sub ? `<p>${esc(sub)}</p>` : ""}</div>
    <button type="button" class="mp-x" data-mp-close aria-label="${t("Закрыть", "Close")}">${ico("close")}</button></div>`;

  function fieldHtml(f, value) {
    const label = en() ? f.en : f.ru;
    const hint = en() ? f.hint_en : f.hint_ru;
    const val = value ?? f.def ?? "";
    let control;
    if (f.options) {
      control = `<select class="mp-input" name="${f.key}">${f.options.map((o) => { const [v, l] = Array.isArray(o) ? o : [o, o]; return `<option value="${esc(v)}"${String(v) === String(val) ? " selected" : ""}>${esc(l)}</option>`; }).join("")}</select>`;
    } else if (f.type === "textarea") {
      control = `<textarea class="mp-input" name="${f.key}" placeholder="${esc(f.placeholder || "")}">${esc(val)}</textarea>`;
    } else {
      let shown = val;
      if (f.type === "datetime-local" && val) { const d = new Date(val); if (!Number.isNaN(+d)) shown = new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16); }
      control = `<input class="mp-input" name="${f.key}" type="${f.type || "text"}" value="${esc(shown)}" placeholder="${esc(f.placeholder || "")}"${f.geo ? ' data-mp-geo autocomplete="off"' : ""}>`;
    }
    return `<label class="mp-field"><span>${esc(label)}${f.required ? " *" : ""}</span>${control}${hint ? `<small>${esc(hint)}</small>` : ""}${f.geo ? '<div class="mp-geo-results"></div>' : ""}</label>`;
  }

  function pickers(root) {
    const UI = window.MemuPlanUI;
    if (!UI || !root) return;
    $$q('input[type="datetime-local"]:not([data-dt-ready])', root).forEach((carrier) => {
      carrier.dataset.dtReady = "1";
      const [d0, t0] = String(carrier.value || "").split("T");
      const wrap = document.createElement("span");
      wrap.className = "mp-inline-row mp-dt";
      const label = carrier.getAttribute("aria-label") || "";
      wrap.innerHTML = `<input class="mp-input mp-dt-day" data-date data-optional="1" value="${esc(d0 || "")}"${label ? ` aria-label="${esc(label)}"` : ""}><input class="mp-input mp-dt-time" data-time data-optional="1" value="${esc((t0 || "").slice(0, 5))}" aria-label="${esc(t("Время", "Time"))}"${d0 ? "" : " disabled"}>`;
      carrier.hidden = true;
      carrier.after(wrap);
      const day = $q(".mp-dt-day", wrap), time = $q(".mp-dt-time", wrap);
      wrap.addEventListener("change", (event) => {
        event.stopPropagation();
        const iso = day.dataset.iso || "";
        time.disabled = !iso;
        if (iso && !time.value) { time.value = "09:00"; time.dataset.lastCommitted = "09:00"; }
        const next = iso ? `${iso}T${time.dataset.lastCommitted || time.value || "09:00"}` : "";
        if (next !== carrier.value) { carrier.value = next; carrier.dispatchEvent(new Event("input", { bubbles: true })); carrier.dispatchEvent(new Event("change", { bubbles: true })); }
      });
      wrap.addEventListener("input", (event) => event.stopPropagation());
    });
    $$q('input[type="time"]', root).forEach((input) => { input.type = "text"; input.dataset.time = ""; input.classList.add("mp-time"); });
    $$q('input[type="date"]', root).forEach((input) => { input.dataset.date = ""; input.dataset.optional = "1"; input.classList.add("mp-date"); });
    UI.enhanceTimes(root);
    UI.enhanceDates(root);
  }

  function pickedValue(input) {
    if (!input) return "";
    if (input.dataset.dateReady) return input.dataset.iso || "";
    if (input.dataset.timeReady) return input.dataset.lastCommitted || "";
    return input.value;
  }

  function localIso(value) {
    const d = new Date(value);
    if (Number.isNaN(+d)) return value;
    const off = -d.getTimezoneOffset(), a = Math.abs(off), pad = (n) => String(n).padStart(2, "0");
    return `${String(value).slice(0, 16)}${off < 0 ? "-" : "+"}${pad(Math.floor(a / 60))}:${pad(a % 60)}`;
  }
  function readForm(root) {
    const out = {};
    $$q("[name]", root).forEach((el) => {
      if (el.type === "checkbox") return;
      let v = el.value.trim();
      if (el.type === "datetime-local" && v) v = localIso(v);
      out[el.name] = v;
    });
    return out;
  }
  function placeChecks(selected) {
    if (LAY()) return LAY().placesField(selected);
    return `<div class="mp-field"><span>${t("Где показывать", "Where to show")}</span><div class="mp-checks">${PLACES.map((p) => `<label class="mp-check"><input type="checkbox" data-mp-place="${p}"${selected.includes(p) ? " checked" : ""}>${ico(placeIcon(p))}${placeName(p)}</label>`).join("")}</div></div>`;
  }
  const readPlaces = (root) => $$q("[data-mp-place]", root).filter((c) => c.checked).map((c) => c.dataset.mpPlace);
  const readSize = (root) => $q("[data-mp-size] [aria-pressed=true]", root)?.dataset.size || "m";
  function bindSeg(root) {
    root.addEventListener("click", (event) => {
      const b = event.target.closest("[data-mp-size] button, [data-mp-seg] button");
      if (!b) return;
      $$q("button", b.parentElement).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    });
  }
  function bindGeo(root) {
    $$q("[data-mp-geo]", root).forEach((input) => {
      const box = input.parentElement.querySelector(".mp-geo-results");
      input.addEventListener("input", () => {
        clearTimeout(state.geoTimer);
        const q = input.value.trim();
        if (q.length < 2) { box.innerHTML = ""; return; }
        state.geoTimer = setTimeout(async () => {
          try {
            const out = await call(`/api/pg/panels-geocode?q=${encodeURIComponent(q)}&lang=${en() ? "en" : "ru"}`);
            box.innerHTML = (out.results || []).slice(0, 5).map((r) => `<button type="button" data-name="${esc(r.name)}" data-lat="${r.lat}" data-lon="${r.lon}" data-country="${esc(r.country || "")}" data-tz="${esc(r.timezone || "")}">${esc(r.name)}<small>${esc([r.admin, r.country].filter(Boolean).join(", "))}</small></button>`).join("");
          } catch (error) { box.innerHTML = ""; }
        }, 280);
      });
      box.addEventListener("click", (event) => {
        const b = event.target.closest("button");
        if (!b) return;
        input.value = b.dataset.name;
        input.dataset.lat = b.dataset.lat; input.dataset.lon = b.dataset.lon;
        input.dispatchEvent(new CustomEvent("mp-geo-pick", { detail: { ...b.dataset } }));
        box.innerHTML = "";
      });
    });
  }

  const newAccountId = () => "a_" + Array.from(crypto.getRandomValues(new Uint8Array(5))).map((b) => b.toString(16).padStart(2, "0")).join("");
  function proxyAccountOptions(provider, selected) {
    const rows = (state.detected?.proxy_accounts || []).filter((a) => provider === "api_budget" ? a.api_budget : provider === "quota_planner" ? a.quota_planner : provider === "proxy" || a.provider === provider);
    const missing = selected && !rows.some((a) => a.id === selected);
    return `<option value="">${esc(t("Выберите аккаунт…", "Choose an account…"))}</option>` +
      (missing ? `<option value="${esc(selected)}" selected>${esc(t("Сохранённый аккаунт — список недоступен", "Saved account — list unavailable"))}</option>` : "") +
      rows.map((a) => `<option value="${esc(a.id)}"${a.id === selected ? " selected" : ""}>${esc(a.provider + " · " + a.label + (a.enabled === false ? t(" (выключен)", " (disabled)") : ""))}</option>`).join("");
  }
  function fillProxyAccounts(root) {
    $$q("[data-acc-proxy]", root).forEach((select) => {
      const selected = select.value || select.dataset.saved || "";
      select.innerHTML = proxyAccountOptions(select.closest(".mp-acc-row").dataset.provider, selected);
    });
  }
  function accountRow(a) {
    const prov = PROVIDERS[a.provider] ? a.provider : "claude";
    const modes = PROVIDERS[prov].modes;
    const mode = modes.includes(a.mode) ? a.mode : modes[0];
    return `<div class="mp-acc-row" data-acc-id="${esc(a.id || newAccountId())}" data-provider="${prov}">
      <div class="mp-acc-line"><span class="mp-acc-ico">${ico(PROVIDERS[prov].icon)}</span>
        <select class="mp-input" data-acc-provider aria-label="${t("Сервис", "Service")}">${Object.entries(PROVIDERS).map(([k, v]) => `<option value="${k}"${k === prov ? " selected" : ""}>${v.name}</option>`).join("")}</select>
        <input class="mp-input" data-acc-name maxlength="40" value="${esc(a.name || "")}" placeholder="${esc(t("Название: «Работа», «Личный»…", "Name: “Work”, “Personal”…"))}" aria-label="${t("Название аккаунта", "Account name")}">
        <button type="button" class="mp-x" data-acc-del title="${t("Убрать аккаунт", "Remove account")}" aria-label="${t("Убрать аккаунт", "Remove account")}">${ico("trash")}</button></div>
      <div class="mp-acc-line"><select class="mp-input" data-acc-mode aria-label="${t("Откуда брать данные", "Where to read from")}">${modes.map((m) => `<option value="${m}"${m === mode ? " selected" : ""}>${esc(modeName(prov, m))}</option>`).join("")}</select>
        <select class="mp-input" data-acc-proxy data-saved="${esc(a.proxy_account_id || "")}"${mode === "proxy" ? "" : " hidden"} aria-label="${t("Аккаунт MemuAI Proxy", "MemuAI Proxy account")}">${proxyAccountOptions(prov, a.proxy_account_id || "")}</select>
        ${mode === "proxy" ? `<button type="button" class="mp-btn" data-acc-reload title="${t("Обновить список аккаунтов", "Reload account list")}" aria-label="${t("Обновить список аккаунтов", "Reload account list")}">${ico("refresh")}</button>` : ""}
        <input class="mp-input" data-acc-dir value="${esc(a.dir || "")}" placeholder="${prov === "antigravity" ? "C:\\Users\\…\\.gemini" : prov === "codex" ? "C:\\Users\\…\\.codex-work" : "C:\\Users\\…\\.claude-work"}" ${mode === "dir" ? "" : "hidden"} aria-label="${t("Папка", "Folder")}">
        <input class="mp-input" data-acc-token type="password" autocomplete="off" spellcheck="false" placeholder="${esc(a.has_token ? t("Токен сохранён — вставьте новый, чтобы заменить", "Token saved — paste a new one to replace") : prov === "codex" ? t("access_token из auth.json (eyJ…)", "access_token from auth.json (eyJ…)") : t("OAuth-токен Claude (sk-ant-oat01-…)", "Claude OAuth token (sk-ant-oat01-…)"))}" ${mode === "token" ? "" : "hidden"} aria-label="${t("Токен", "Token")}">
        <button type="button" class="mp-btn" data-acc-test>${ico("refresh")}${t("Проверить", "Test")}</button></div>
      ${mode === "proxy" ? `<small class="mp-acc-notice">${t("Аккаунты и ключи настраиваются в Proxy. Баланс API — приблизительная оценка расходов.", "Manage accounts and keys in Proxy. API balance is an approximate spending estimate.")}</small>` : ""}<div class="mp-acc-result" aria-live="polite"></div></div>`;
  }
  function detectedLine() {
    const d = state.detected;
    if (!d) return `<span class="mp-detect-wait"><span class="mp-spin"></span>${t("Ищу Claude Code, Codex и Antigravity на этом компьютере…", "Looking for Claude Code, Codex and Antigravity on this PC…")}</span>`;
    const parts = [];
    const cli = (d.claude || []).find((x) => x.mode === "cli");
    const desk = (d.claude || []).find((x) => x.mode === "desktop");
    const cx = (d.codex || [])[0];
    const ag = (d.antigravity || [])[0];
    parts.push(`<span data-ok="${!!cli?.found}">${ico(cli?.found ? "check" : "close")}Claude Code${cli?.plan ? " · " + esc(cli.plan) : ""}</span>`);
    if (desk?.present) parts.push(`<span data-ok="${!!desk.found}">${ico(desk.found ? "check" : "close")}Claude Desktop</span>`);
    parts.push(`<span data-ok="${!!cx?.found}">${ico(cx?.found ? "check" : "close")}Codex${cx?.api_key_only ? t(" (API-ключ)", " (API key)") : cx?.plan ? " · " + esc(cx.plan) : ""}</span>`);
    parts.push(`<span data-ok="${!!ag?.running}">${ico(ag?.running ? "check" : "close")}Antigravity${ag?.running ? "" : t(" не запущен", " not running")}</span>`);
    return parts.join("");
  }
  function accountsEditor(accounts) {
    const rows = (accounts && accounts.length ? accounts : [{ provider: "claude", mode: "auto" }]).map(accountRow).join("");
    return `<div class="mp-field" data-mp-accounts><span>${t("Аккаунты", "Accounts")}</span>
      <div class="mp-detect">${detectedLine()}</div>
      <div class="mp-acc-rows">${rows}</div>
      <button type="button" class="mp-btn" data-acc-add style="justify-self:start">${ico("plus")}${t("Добавить аккаунт", "Add an account")}</button>
      <small>${t("Для Kiro и других источников Proxy выберите уже подключённый аккаунт. Для API-баланса сначала настройте его в планировщике квот. Отдельные локальные источники Claude/Codex/Antigravity по-прежнему доступны; их файлы не изменяются. Вставленный токен хранится отдельно от панели и на Windows защищён DPAPI. Приблизительный баланс API не учитывает запросы в обход Proxy.",
        "For Kiro and other Proxy sources, select an existing account. Set up an API balance in the quota planner first. Local Claude/Codex/Antigravity sources remain available and their files are never modified. A pasted token is stored separately and protected with Windows DPAPI. Estimated API balances exclude requests bypassing Proxy.")}</small></div>`;
  }
  function readAccounts(root) {

    const rows = root.matches?.(".mp-acc-row") ? [root] : $$q(".mp-acc-row", root);
    return rows.map((row) => ({
      id: row.dataset.accId, provider: $q("[data-acc-provider]", row).value, name: $q("[data-acc-name]", row).value.trim(),
      mode: $q("[data-acc-mode]", row).value, dir: $q("[data-acc-dir]", row).value.trim(), token: $q("[data-acc-token]", row).value.trim(),
      proxy_account_id: $q("[data-acc-proxy]", row)?.value || "",
    }));
  }
  async function saveTokens(accounts) {
    for (const a of accounts) {
      if (a.mode === "token" && a.token) await call("/api/pg/panels-usage/secret", { method: "POST", body: { id: a.id, token: a.token } });
    }
    return accounts.map(({ token, ...rest }) => rest);
  }
  function bindAccounts(root) {
    const box = $q("[data-mp-accounts]", root);
    if (!box) return;
    if (!state.detected) {
      call("/api/pg/panels-usage/detect").then((out) => { state.detected = out.detected || {}; fillProxyAccounts(box); const d = $q(".mp-detect", box); if (d) d.innerHTML = detectedLine(); })
        .catch(() => { state.detected = {}; const d = $q(".mp-detect", box); if (d) d.innerHTML = ""; });
    }
    box.addEventListener("change", (event) => {
      const row = event.target.closest(".mp-acc-row");
      if (!row) return;
      if (event.target.matches("[data-acc-proxy]")) {
        event.target.dataset.saved = event.target.value;
      } else if (event.target.matches("[data-acc-provider]")) {
        const a = readAccounts(row)[0] || {};

        row.outerHTML = accountRow({ ...a, id: newAccountId(), provider: event.target.value, mode: "auto", token: "", proxy_account_id: "" });
      } else if (event.target.matches("[data-acc-mode]")) {
        $q("[data-acc-dir]", row).hidden = event.target.value !== "dir";
        $q("[data-acc-token]", row).hidden = event.target.value !== "token";
      }
    });
    box.addEventListener("click", async (event) => {
      const reload = event.target.closest("[data-acc-reload]");
      if (reload) {
        reload.disabled = true;
        try { state.detected = (await call("/api/pg/panels-usage/detect")).detected || {}; fillProxyAccounts(box); }
        catch (error) { notify(error.message || String(error), "err"); }
        finally { reload.disabled = false; }
        return;
      }
      if (event.target.closest("[data-acc-add]")) {
        const used = new Set(readAccounts(box).map((a) => a.provider));
        const next = ["claude", "codex", "antigravity"].find((p) => !used.has(p)) || "claude";
        $q(".mp-acc-rows", box).insertAdjacentHTML("beforeend", accountRow({ provider: next, mode: "auto" }));
        return;
      }
      const row = event.target.closest(".mp-acc-row");
      if (!row) return;
      if (event.target.closest("[data-acc-del]")) {
        if ($$q(".mp-acc-row", box).length > 1) row.remove(); else notify(t("Нужен хотя бы один аккаунт", "At least one account is needed"), "err");
        return;
      }
      const test = event.target.closest("[data-acc-test]");
      if (!test) return;
      const result = $q(".mp-acc-result", row);
      test.disabled = true;
      result.innerHTML = `<span class="mp-spin"></span>${t("Проверяю…", "Checking…")}`;
      try {
        const [a] = await saveTokens(readAccounts(row));
        const out = await call("/api/pg/panels-usage/test", { method: "POST", body: { ...a, lang: en() ? "en" : "ru" } });
        const acc = out.account || {};
        result.dataset.state = acc.state || "";
        result.innerHTML = acc.state === "ok"
          ? `${ico("check")}${esc([acc.plan, ...(acc.limits || []).map((l) => `${l.title}: ${Math.round(l.used)}%`)].filter(Boolean).join(" · "))}`
          : `${ico("alert")}${esc(acc.wall?.message || t("Нет данных", "No data"))}`;
        const tok = $q("[data-acc-token]", row);
        if (tok.value) { tok.value = ""; tok.placeholder = t("Токен сохранён — вставьте новый, чтобы заменить", "Token saved — paste a new one to replace"); }
      } catch (error) {
        result.dataset.state = "wall";
        result.innerHTML = `${ico("alert")}${esc(error.message || String(error))}`;
      } finally { test.disabled = false; }
    });
  }

  function newsFieldsSync(root) {
    const topic = $q("select[name=topic]", root);
    const brief = $q("[name=brief]", root);
    if (!topic || !brief) return;
    const label = brief.closest(".mp-field")?.querySelector(":scope > span");
    const update = () => { if (label) label.textContent = topic.value ? t("Уточнение (необязательно)", "Refine (optional)") : t("Своя тема *", "Your topic *"); };
    topic.addEventListener("change", update); update();
  }

  function progressModal(id) {
    const back = state.modal;
    if (!back) return;
    const panel = byId(id);
    back.dataset.progressId = id;
    const modal = $q(".mp-modal", back);
    modal.classList.add("narrow");
    modal.innerHTML = `${head(panel?.title || t("Новая панель", "New panel"), t("Собираю данные — карточка уже на месте и заполнится сама.", "Gathering data — the card is already in place and will fill in by itself."))}
      <div class="mp-modal-body"><div class="mp-preview-live"></div></div>
      <div class="mp-modal-foot"><button type="button" class="mp-btn primary" data-mp-close>${t("Свернуть", "Hide")}</button></div>`;
    renderProgressModal();
  }
  function renderProgressModal() {
    const back = state.modal;
    const id = back?.dataset.progressId;
    if (!id) return;
    const panel = byId(id);
    const box = $q(".mp-preview-live", back);
    if (!panel || !box) return;
    box.innerHTML = cardHtml(panel, "home", { static: true, size: panel.size === "s" ? "m" : panel.size || "m", place: "modal" });
    const done = !(panel.status === "building" || panel.refreshing);
    const btn = $q("[data-mp-close]", back);
    if (done && btn) {
      btn.textContent = t("Готово", "Done");
      if (panel.status === "live" && !panel.wall && !back.dataset.closing) {
        back.dataset.closing = "1";
        setTimeout(() => { if (state.modal === back) { closeModal(); flash(id); } }, 1100);
      }
    }
  }

  const L = (pair) => (Array.isArray(pair) ? (en() ? pair[1] : pair[0]) : String(pair || ""));
  const choiceLabel = (c) => (en() ? c[2] : c[1]);
  const TPL_CADENCE = { weather: { kind: "interval", minutes: 60 }, currency: { kind: "interval", minutes: 180 }, crypto: { kind: "interval", minutes: 60 },
    stock: { kind: "interval", minutes: 60 }, rss: { kind: "interval", minutes: 120 }, sports: { kind: "interval", minutes: 120 }, web: { kind: "interval", minutes: 360 },
    custom: { kind: "daily", at: "08:00" }, news: { kind: "daily", at: "08:00" }, system: { kind: "interval", minutes: 5 }, usage: { kind: "interval", minutes: 5 },
    proxy: { kind: "interval", minutes: 5 }, calendar: { kind: "interval", minutes: 30 }, github: { kind: "interval", minutes: 15 } };
  const TZ_SUGGEST = ["Europe/Moscow|Москва", "Europe/London|Лондон", "Europe/Berlin|Берлин", "Europe/Paris|Париж", "Europe/Istanbul|Стамбул",
    "Asia/Dubai|Дубай", "Asia/Almaty|Алматы", "Asia/Tashkent|Ташкент", "Asia/Yekaterinburg|Екатеринбург", "Asia/Novosibirsk|Новосибирск",
    "Asia/Vladivostok|Владивосток", "Asia/Shanghai|Пекин", "Asia/Tokyo|Токио", "Asia/Kolkata|Дели", "Asia/Singapore|Сингапур",
    "America/New_York|Нью-Йорк", "America/Chicago|Чикаго", "America/Los_Angeles|Лос-Анджелес", "America/Sao_Paulo|Сан-Паулу",
    "Australia/Sydney|Сидней", "UTC|UTC"];
  function defaultsOf(kind) {
    const out = {};
    (state.schema[kind] || []).forEach((o) => { out[o.key] = Array.isArray(o.default) ? o.default.slice() : (o.default && typeof o.default === "object" ? { ...o.default } : o.default); });
    return out;
  }
  function optRow(o, value, kind, panel) {
    const label = L(o.label), hint = o.hint ? L(o.hint) : "";
    const when = o.when ? ` data-when='${esc(JSON.stringify(o.when))}'` : "";
    const hintHtml = hint ? `<small>${esc(hint)}</small>` : "";
    if (o.type === "bool") {
      return `<label class="mp-switchrow" data-opt-wrap="${o.key}"${when}><span>${esc(label)}${hintHtml}</span><input type="checkbox" class="mp-switch" role="switch" data-opt="${o.key}" data-type="bool"${value ? " checked" : ""}></label>`;
    }
    if (o.type === "choice") {
      if (o.style === "seg" && (o.choices || []).length <= 6) {
        return `<div class="mp-field" data-opt-wrap="${o.key}"${when}><span>${esc(label)}</span><div class="mp-seg mp-seg-wrap" data-opt="${o.key}" data-type="choice" role="radiogroup" aria-label="${esc(label)}">${o.choices.map((c) => `<button type="button" role="radio" data-v="${esc(c[0])}" aria-pressed="${String(value) === String(c[0])}" aria-checked="${String(value) === String(c[0])}">${esc(choiceLabel(c))}</button>`).join("")}</div>${hintHtml}</div>`;
      }
      return `<label class="mp-field" data-opt-wrap="${o.key}"${when}><span>${esc(label)}</span><select class="mp-input" data-opt="${o.key}" data-type="choice" data-no-search>${o.choices.map((c) => `<option value="${esc(c[0])}"${String(value) === String(c[0]) ? " selected" : ""}>${esc(choiceLabel(c))}</option>`).join("")}</select>${hintHtml}</label>`;
    }
    if (o.type === "multi") {
      const chosen = Array.isArray(value) ? value : [];
      const byV = new Map(o.choices.map((c) => [c[0], c]));
      const ordered = [...chosen.filter((v) => byV.has(v)).map((v) => byV.get(v)), ...o.choices.filter((c) => !chosen.includes(c[0]))];
      const sortable = kind === "system" && o.key === "metrics";
      return `<div class="mp-field" data-opt-wrap="${o.key}"${when}><span>${esc(label)}</span><div class="mp-multi${sortable ? " sortable" : ""}" data-opt="${o.key}" data-type="multi" role="group" aria-label="${esc(label)}">${ordered.map((c) => `<label class="mp-multi-row" data-v="${esc(c[0])}"${sortable ? ' draggable="true"' : ""}><input type="checkbox"${chosen.includes(c[0]) ? " checked" : ""}><span>${esc(choiceLabel(c))}</span>${sortable ? `<span class="mp-grip" title="${esc(t("Перетащите или Alt+↑/↓", "Drag or Alt+↑/↓"))}">${ico("drag")}</span>` : ""}</label>`).join("")}</div>${hintHtml}</div>`;
    }
    if (o.type === "int") {
      return `<label class="mp-field mp-field-num" data-opt-wrap="${o.key}"${when}><span>${esc(label)}</span><input class="mp-input" type="number" data-opt="${o.key}" data-type="int" min="${o.min ?? ""}" max="${o.max ?? ""}" step="${o.step || 1}" value="${esc(value)}">${hintHtml}</label>`;
    }
    if (o.type === "text" || o.type === "list") {
      const shown = Array.isArray(value) ? value.join(", ") : (value ?? "");
      const list = kind === "clock" && o.key === "zones" ? ' list="mp-tz-list"' : "";
      return `<label class="mp-field" data-opt-wrap="${o.key}"${when}><span>${esc(label)}</span><input class="mp-input" data-opt="${o.key}" data-type="${o.type}" value="${esc(shown)}" placeholder="${esc(o.placeholder ? L(o.placeholder) : "")}"${list} autocomplete="off" spellcheck="false">${hintHtml}</label>`;
    }
    if (o.type === "map") {
      const metrics = (panel?.__metrics || []).filter((m) => (o.views || {})[m]?.length > 1);
      if (!metrics.length) return `<div data-opt-wrap="${o.key}" data-opt="${o.key}" data-type="map" hidden></div>`;
      const names = { cpu: t("Процессор", "CPU"), ram: t("Память", "Memory"), swap: t("Подкачка", "Swap"), gpu: t("Видеокарта", "GPU"), vram: t("Видеопамять", "VRAM"),
        gpu_temp: t("Темп. видеокарты", "GPU temp"), disks: t("Диски", "Disks"), disk_io: t("Скорость дисков", "Disk speed"), net: t("Сеть", "Network"),
        temps: t("Температуры", "Temperatures"), battery: t("Батарея", "Battery") };
      const viewName = { ring: t("Кольцо", "Ring"), bar: t("Полоса", "Bar"), number: t("Число", "Number"), chart: t("График", "Chart") };
      return `<div class="mp-field" data-opt-wrap="${o.key}"${when}><span>${esc(label)}</span><div class="mp-views" data-opt="${o.key}" data-type="map">${metrics.map((m) => `<label class="mp-view-row"><span>${esc(names[m] || m)}</span><select class="mp-input" data-view="${esc(m)}" data-no-search><option value="">${t("Авто", "Auto")}</option>${o.views[m].map((v) => `<option value="${v}"${(value || {})[m] === v ? " selected" : ""}>${esc(viewName[v] || v)}</option>`).join("")}</select></label>`).join("")}</div>${hintHtml}</div>`;
    }
    if (o.type === "lists") {
      const all = panel?.todo?.all || state.todoAll || [];
      const chosen = Array.isArray(value) ? value : [];
      return `<div class="mp-field" data-opt-wrap="${o.key}"${when}><span>${esc(label)}</span><div class="mp-multi" data-opt="${o.key}" data-type="lists">${all.map((l) => `<label class="mp-multi-row" data-v="${esc(l.id)}"><input type="checkbox"${chosen.includes(l.id) ? " checked" : ""}><span>${esc(l.name)}</span><button type="button" class="mp-x" data-list-rename="${esc(l.id)}" title="${esc(t("Переименовать", "Rename"))}" aria-label="${esc(t("Переименовать", "Rename"))}">${ico("edit")}</button></label>`).join("") || `<small>${t("Списков пока нет — будет создан первый.", "No lists yet — the first one will be created.")}</small>`}
        <div class="mp-inline-row"><input class="mp-input" data-new-list placeholder="${esc(t("Новый список: «Работа», «Покупки»…", "New list: “Work”, “Groceries”…"))}" maxlength="60"><button type="button" class="mp-btn" data-add-list>${ico("plus")}${t("Добавить", "Add")}</button></div></div>${hintHtml}</div>`;
    }
    return "";
  }
  function optionsHtml(kind, values, group, panel) {
    const rows = (state.schema[kind] || []).filter((o) => (o.group || "main") === group);
    if (!rows.length) return "";
    const ctx = { ...(panel || {}), __metrics: values.metrics || [] };
    const groupTitle = t("Что показывать", "What to show");

    return rows.map((o) => { const html = optRow(o, values[o.key], kind, ctx); return group === "main" && L(o.label) === groupTitle ? html.replace(`<span>${esc(groupTitle)}</span>`, "") : html; }).join("");
  }
  function readOptions(root, kind) {
    const out = {};
    $$q("[data-opt]", root).forEach((el) => {
      const key = el.dataset.opt, type = el.dataset.type;
      if (type === "bool") out[key] = el.checked;
      else if (type === "choice") out[key] = el.tagName === "SELECT" ? el.value : ($q("[aria-pressed=true]", el)?.dataset.v ?? "");
      else if (type === "multi" || type === "lists") out[key] = $$q(".mp-multi-row", el).filter((r) => $q("input", r).checked).map((r) => r.dataset.v);
      else if (type === "int") out[key] = +el.value;
      else if (type === "text") out[key] = el.value.trim();
      else if (type === "list") out[key] = el.value.split(/[,;\n]+/).map((x) => x.trim()).filter(Boolean);
      else if (type === "map") { const m = {}; $$q("[data-view]", el).forEach((s) => { if (s.value) m[s.dataset.view] = s.value; }); out[key] = m; }
    });
    void kind;
    return out;
  }
  function applyWhen(root) {
    const values = readOptions(root);
    $$q("[data-when]", root).forEach((el) => {
      let rule = {};
      try { rule = JSON.parse(el.dataset.when); } catch (e) { rule = {}; }
      const ok = Object.entries(rule).every(([key, allowed]) => {
        const v = values[key];
        if (v === undefined) return true;
        return Array.isArray(v) ? v.some((x) => allowed.includes(x)) : allowed.includes(String(v));
      });
      el.hidden = !ok;
    });
  }
  function bindOptions(root, kind, onChange) {
    const changed = () => { applyWhen(root); onChange?.(); };
    root.addEventListener("click", (event) => {
      const seg = event.target.closest(".mp-seg[data-opt] button");
      if (seg) { $$q("button", seg.parentElement).forEach((b) => { b.setAttribute("aria-pressed", String(b === seg)); b.setAttribute("aria-checked", String(b === seg)); }); changed(); }
    });
    root.addEventListener("change", (event) => { if (event.target.closest("[data-opt]")) changed(); });
    root.addEventListener("input", (event) => { if (event.target.closest('[data-opt][data-type="text"], [data-opt][data-type="list"], [data-opt][data-type="int"]')) { clearTimeout(root._optT); root._optT = setTimeout(changed, 400); } });

    let dragRow = null;
    root.addEventListener("dragstart", (event) => { const row = event.target.closest?.(".mp-multi.sortable .mp-multi-row"); if (!row) return; dragRow = row; event.stopPropagation(); event.dataTransfer.effectAllowed = "move"; row.classList.add("dragging"); });
    root.addEventListener("dragover", (event) => {
      if (!dragRow) return;
      const over = event.target.closest?.(".mp-multi-row");
      if (!over || over === dragRow || over.parentElement !== dragRow.parentElement) return;
      event.preventDefault(); event.stopPropagation();
      const r = over.getBoundingClientRect();
      over.parentElement.insertBefore(dragRow, event.clientY < r.top + r.height / 2 ? over : over.nextSibling);
    });
    root.addEventListener("dragend", () => { if (dragRow) { dragRow.classList.remove("dragging"); dragRow = null; changed(); } });
    root.addEventListener("drop", (event) => { if (dragRow) { event.preventDefault(); event.stopPropagation(); } });
    root.addEventListener("keydown", (event) => {
      const row = event.target.closest?.(".mp-multi.sortable .mp-multi-row");
      if (!row || !event.altKey || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
      event.preventDefault();
      const sib = event.key === "ArrowUp" ? row.previousElementSibling : row.nextElementSibling;
      if (sib) { row.parentElement.insertBefore(row, event.key === "ArrowUp" ? sib : sib.nextSibling); event.target.focus(); changed(); }
    });

    root.addEventListener("click", async (event) => {
      const rename = event.target.closest("[data-list-rename]");
      if (rename) {
        event.preventDefault();
        const row = rename.closest(".mp-multi-row");
        const name = prompt(t("Новое название списка", "New list name"), $q("span", row)?.textContent || "");
        if (name && name.trim()) {
          const out = await todoCall(`/api/pg/panels-todo/${rename.dataset.listRename}`, { method: "PATCH", body: { name: name.trim() } });
          if (out?.list) { $q("span", row).textContent = out.list.name; todoSync(); }
        }
        return;
      }
      if (!event.target.closest("[data-add-list]")) return;
      const input = $q("[data-new-list]", root);
      const name = input?.value.trim();
      if (!name) { input?.focus(); return; }
      const out = await todoCall("/api/pg/panels-todo", { method: "POST", body: { name } });
      if (!out?.list) return;
      input.value = "";
      const box = $q('[data-opt][data-type="lists"]', root);
      box.querySelector("small")?.remove();
      box.querySelector(".mp-inline-row").insertAdjacentHTML("beforebegin", `<label class="mp-multi-row" data-v="${esc(out.list.id)}"><input type="checkbox" checked><span>${esc(out.list.name)}</span></label>`);
      state.todoAll = [...(state.todoAll || []), { id: out.list.id, name: out.list.name, color: out.list.color }];
      changed();
    });
    root.addEventListener("keydown", (event) => { if (event.key === "Enter" && event.target.matches("[data-new-list]")) { event.preventDefault(); $q("[data-add-list]", root)?.click(); } });
    applyWhen(root);
  }

  function floorOf(kind) { return +(state.floors?.[kind] ?? (["sports", "web", "custom", "news"].includes(kind) ? 15 : 1)) || 1; }
  function floorReason(kind) { const r = state.floorReasons?.[kind]; return r ? (en() ? r[1] : r[0]) : ""; }
  function cadenceSelect(c, kind) {
    if (kind === "countdown" || LOCAL_KINDS.includes(kind)) return "";
    c = c || TPL_CADENCE[kind] || { kind: "interval", minutes: 60 };
    const floor = floorOf(kind);
    const presets = (state.presets || []).filter((m) => m >= floor);
    let mode = c.kind === "daily" ? "daily" : c.kind === "times" ? "times" : c.kind === "manual" ? "manual" : String(c.minutes || 60);
    if (c.kind === "interval" && !presets.includes(+c.minutes)) mode = "custom";
    const n = +c.minutes || 60;
    const unit = n % 1440 === 0 ? "d" : n % 60 === 0 ? "h" : "m";
    const nShown = unit === "d" ? n / 1440 : unit === "h" ? n / 60 : n;
    const times = c.kind === "times" ? c.at || [] : c.kind === "daily" ? [c.at || "08:00"] : ["08:00"];
    const days = Array.isArray(c.days) ? c.days : [];
    const opts = presets.map((m) => `<option value="${m}"${mode === String(m) ? " selected" : ""}>${esc(cadenceLabel({ kind: "interval", minutes: m }))}</option>`).join("")
      + `<option value="custom"${mode === "custom" ? " selected" : ""}>${t("Свой интервал…", "Custom interval…")}</option>`
      + `<option value="daily"${mode === "daily" ? " selected" : ""}>${t("Раз в день в…", "Once a day at…")}</option>`
      + `<option value="times"${mode === "times" ? " selected" : ""}>${t("В своё время и по дням…", "At set times and days…")}</option>`
      + `<option value="manual"${mode === "manual" ? " selected" : ""}>${t("Только вручную", "Manual only")}</option>`;
    const dayChips = `<div class="mp-days" data-cad-days role="group" aria-label="${esc(t("Дни недели", "Days of week"))}">${DAY_SHORT().map((d, i) => `<button type="button" data-day="${i}" aria-pressed="${!days.length || days.includes(i)}">${d}</button>`).join("")}
      <span class="mp-days-quick"><button type="button" class="mp-linkbtn" data-days="0,1,2,3,4">${t("будни", "weekdays")}</button><button type="button" class="mp-linkbtn" data-days="0,1,2,3,4,5,6">${t("все", "all")}</button></span></div>`;
    const windowOn = !!(c.from && c.to);
    return `<div class="mp-field mp-cad" data-mp-cad data-kind="${esc(kind)}"><span>${t("Как часто обновлять", "How often to refresh")}</span>
      <select class="mp-input" data-mp-cadence data-no-search>${opts}</select>
      <div class="mp-inline-row" data-cad-custom ${mode === "custom" ? "" : "hidden"}><span class="mp-inline-label">${t("Каждые", "Every")}</span><input class="mp-input" type="number" min="1" max="10080" data-cad-n value="${esc(nShown)}" aria-label="${esc(t("Число", "Number"))}">
        <select class="mp-input" data-cad-unit data-no-search aria-label="${esc(t("Единица", "Unit"))}">${[["s", t("секунд", "seconds")], ["m", t("минут", "minutes")], ["h", t("часов", "hours")], ["d", t("дней", "days")]].map(([v, l]) => `<option value="${v}"${v === unit ? " selected" : ""}>${l}</option>`).join("")}</select></div>
      <div class="mp-inline-row" data-cad-daily ${mode === "daily" ? "" : "hidden"}><span class="mp-inline-label">${t("В", "At")}</span><input class="mp-input" type="time" data-mp-at value="${esc(c.kind === "daily" ? c.at : "08:00")}"></div>
      <div data-cad-times ${mode === "times" ? "" : "hidden"}><div class="mp-times">${times.map((x) => `<span class="mp-time-chip" data-time="${esc(x)}">${esc(x)}<button type="button" data-time-del aria-label="${esc(t("Убрать", "Remove"))}">${ico("close")}</button></span>`).join("")}
        <span class="mp-time-add"><input class="mp-input" type="time" data-time-new value="13:00" aria-label="${esc(t("Время", "Time"))}"><button type="button" class="mp-btn" data-time-add>${ico("plus")}</button></span></div>${dayChips}</div>
      <details class="mp-cad-more" data-cad-more ${["daily", "manual", "times"].includes(mode) ? "hidden" : ""}${days.length || windowOn ? " open" : ""}><summary>${t("Только в некоторые дни и часы", "Only on some days and hours")}</summary>
        ${mode === "times" ? "" : dayChips.replace("data-cad-days", "data-cad-days2")}
        <label class="mp-inline-row"><input type="checkbox" data-cad-win${windowOn ? " checked" : ""}><span>${t("С", "From")}</span><input class="mp-input" type="time" data-cad-from value="${esc(c.from || "09:00")}"><span>${t("до", "to")}</span><input class="mp-input" type="time" data-cad-to value="${esc(c.to || "18:00")}"></label></details>
      <small data-cad-hint>${esc(floorHint(kind))}</small></div>`;
  }
  function floorHint(kind, minutes) {
    const floor = floorOf(kind);
    const reason = floorReason(kind);
    if (minutes != null && minutes < floor) {
      return t(`Для этой панели не чаще раза в ${floor} мин — сохранится ${floor} мин. ${reason}`, `This panel refreshes at most every ${floor} min — ${floor} min will be saved. ${reason}`);
    }
    if (kind === "system") return t("Пока карточка на экране, значения идут в живом режиме (1–5 с); ритм — для мини-чипа и истории.", "While the card is on screen values come in Live mode (1–5 s); the cadence is for the mini chip and history.");
    return floor > 1 ? t(`Не чаще раза в ${floor} мин. ${reason}`, `At most every ${floor} min. ${reason}`) : reason;
  }
  function cadenceMinutes(root) {
    const n = Math.max(1, +$q("[data-cad-n]", root)?.value || 1);
    const unit = $q("[data-cad-unit]", root)?.value || "m";
    return unit === "s" ? n / 60 : unit === "h" ? n * 60 : unit === "d" ? n * 1440 : n;
  }
  function readDays(box) {
    if (!box) return [];
    const on = $$q("[data-day]", box).filter((b) => b.getAttribute("aria-pressed") === "true").map((b) => +b.dataset.day);
    return on.length === 7 ? [] : on;
  }
  function readCadence(root) {
    const cad = $q("[data-mp-cad]", root);
    const sel = $q("[data-mp-cadence]", root);
    if (!sel || !cad) return undefined;
    const kind = cad.dataset.kind;
    const mode = sel.value;
    if (mode === "manual") return { kind: "manual" };
    if (mode === "daily") return { kind: "daily", at: pickedValue($q("[data-mp-at]", cad)) || "08:00" };
    if (mode === "times") {
      const at = $$q(".mp-time-chip[data-time]", cad).map((x) => x.dataset.time);
      return { kind: "times", at: at.length ? at : ["08:00"], days: readDays($q("[data-cad-days]", cad)) };
    }
    const minutes = mode === "custom" ? Math.max(floorOf(kind), Math.ceil(cadenceMinutes(cad))) : +mode || 60;
    const out = { kind: "interval", minutes };
    const days = readDays($q("[data-cad-days2]", cad));
    if (days.length) out.days = days;
    if ($q("[data-cad-win]", cad)?.checked) { out.from = pickedValue($q("[data-cad-from]", cad)); out.to = pickedValue($q("[data-cad-to]", cad)); }
    return out;
  }
  function bindCadence(root) {
    const cad = $q("[data-mp-cad]", root);
    if (!cad) return;
    const kind = cad.dataset.kind;
    const sync = () => {
      const mode = $q("[data-mp-cadence]", cad).value;
      $q("[data-cad-custom]", cad).hidden = mode !== "custom";
      $q("[data-cad-daily]", cad).hidden = mode !== "daily";
      $q("[data-cad-times]", cad).hidden = mode !== "times";
      $q("[data-cad-more]", cad).hidden = ["daily", "manual", "times"].includes(mode);
      const hint = $q("[data-cad-hint]", cad);
      let text = floorHint(kind);
      let warn = false;
      if (mode === "custom") {
        const m = cadenceMinutes(cad);
        if (m < floorOf(kind)) { text = floorHint(kind, m); warn = true; }
        if (kind === "system" && m < 1) text += " " + t("Для обновления чаще минуты включите «Живой режим» выше.", "For faster than a minute turn on Live mode above.");
      }
      if (mode === "manual") text = t("Обновляется только по «Обновить сейчас».", "Refreshes only on “Refresh now”.");
      if (mode === "times" && !$$q(".mp-time-chip[data-time]", cad).length) { text = t("Добавьте хотя бы одно время.", "Add at least one time."); warn = true; }
      hint.textContent = text;
      hint.dataset.warn = warn ? "1" : "";
    };
    cad.addEventListener("change", sync);
    cad.addEventListener("input", sync);
    cad.addEventListener("click", (event) => {
      const day = event.target.closest("[data-day]");
      if (day) { day.setAttribute("aria-pressed", String(day.getAttribute("aria-pressed") !== "true")); if (!$$q("[data-day][aria-pressed=true]", day.parentElement).length) day.setAttribute("aria-pressed", "true"); sync(); return; }
      const quick = event.target.closest("[data-days]");
      if (quick) { const on = quick.dataset.days.split(",").map(Number); $$q("[data-day]", quick.closest(".mp-days")).forEach((b) => b.setAttribute("aria-pressed", String(on.includes(+b.dataset.day)))); sync(); return; }
      if (event.target.closest("[data-time-add]")) {
        const v = pickedValue($q("[data-time-new]", cad));
        if (v && !$$q(".mp-time-chip[data-time]", cad).some((x) => x.dataset.time === v)) {
          const chips = $$q(".mp-time-chip[data-time]", cad).map((x) => x.dataset.time).concat(v).sort();
          const box = $q(".mp-times", cad);
          $$q(".mp-time-chip[data-time]", box).forEach((x) => x.remove());
          box.insertAdjacentHTML("afterbegin", chips.map((x) => `<span class="mp-time-chip" data-time="${esc(x)}">${esc(x)}<button type="button" data-time-del aria-label="${esc(t("Убрать", "Remove"))}">${ico("close")}</button></span>`).join(""));
        }
        sync(); return;
      }
      const del = event.target.closest("[data-time-del]");
      if (del) { del.closest(".mp-time-chip").remove(); sync(); }
    });
    sync();
  }

  function chatModels() {
    return (typeof S !== "undefined" && Array.isArray(S.models) ? S.models : [])
      .filter((m) => !(typeof isImageModel === "function" && isImageModel(m.id)) && !(typeof isMusicOnlyModel === "function" && isMusicOnlyModel(m.id)));
  }
  const modelName = (id) => (typeof modelDisplayName === "function" ? modelDisplayName(id) : id);

  function modelPicker(current, { inheritLabel, attr, label, hint }) {
    const models = chatModels();
    const has = models.some((m) => m.id === current);
    const disabled = !models.length;
    const note = disabled ? t("Список моделей недоступен: MemuAI Proxy не запущен или ещё не ответил. Выбранная модель сохранится.", "The model list is unavailable: MemuAI Proxy is not running or has not answered yet. The chosen model is kept.") : hint || "";
    return `<label class="mp-field"><span>${esc(label)}</span><select class="mp-input" ${attr}${disabled ? " disabled" : ""} data-no-search><option value="">${esc(inheritLabel)}</option>${models.map((m) => `<option value="${esc(m.id)}"${m.id === current ? " selected" : ""}>${esc(modelName(m.id))}</option>`).join("")}${current && !has ? `<option value="${esc(current)}" selected>${esc(current)}${disabled ? "" : t(" (нет в Proxy)", " (not in Proxy)")}</option>` : ""}</select>${note ? `<small${disabled ? ' data-warn="1"' : ""}>${esc(note)}</small>` : ""}</label>`;
  }
  const globalModelLabel = () => {
    const g = state.settings?.model;
    return g ? t(`Как в настройках панелей: ${modelName(g)}`, `As in panel settings: ${modelName(g)}`) : t("Как в настройках панелей (основная модель)", "As in panel settings (the default model)");
  };

  function sizeSeg(size, kind) {
    const sizes = ["s", "m", "l", "w"];
    return `<div class="mp-field"><span>${LAY() ? t("Размер карточки", "Card size") : t("Размер на главном экране", "Size on the home screen")}</span><div class="mp-seg mp-seg-size" data-mp-size>${sizes.map((s) => `<button type="button" data-size="${s}" aria-pressed="${size === s}" title="${esc(SIZE_HINTS()[s])}">${esc(SIZE_NAMES()[s])}</button>`).join("")}</div><small data-size-hint>${esc(SIZE_HINTS()[size] || "")}</small></div>`;
  }

  const IDEAS = () => (en() ? {
    custom: ["The gold price and its daily change", "Bitcoin dominance and the Fear & Greed index", "Brent oil price in dollars",
      "Gas price at stations in my city", "The latest version of Python and its release date", "Number of GitHub stars of the MemuAI repository",
      "Today's sunrise and sunset and day length", "Moon phase today", "Air quality index (AQI) in my city", "Geomagnetic storm forecast for today",
      "Next SpaceX launch: rocket, mission and time", "The ISS position right now", "Next public holiday in Russia and days left",
      "Top 5 trending repositories on GitHub today", "New releases of models on Hugging Face this week", "Latest Steam sale and its end date",
      "Box office top 5 of the weekend", "Upcoming premieres in cinemas this week", "Price of the iPhone 17 Pro at major stores",
      "Mortgage rate of the central bank", "The key rate of the Bank of Russia and the next meeting date", "Inflation in Russia last month",
      "Number of open vacancies for Python developers", "Average salary of a frontend developer", "Traffic jam score in Moscow right now",
      "Last chess world championship game result", "Formula 1: next race and the current standings leader", "UFC: next tournament and main fight",
      "NBA: last night's results", "Champions League: next round matches", "Weather on the weekend in Sochi",
      "Water temperature at the sea in Sochi", "Snow depth at Sheregesh ski resort", "Pollen forecast for allergy sufferers",
      "Word of the day from an online dictionary", "On this day in history (Wikipedia)", "The most read Wikipedia article today",
      "Hacker News top 5 right now", "Product Hunt top launches today", "Status of OpenAI, Anthropic and Google AI services",
      "Status page of GitHub: incidents right now", "Latest Windows 11 update and what it changed", "Latest NVIDIA driver version",
      "Next Apple event date", "Exchange rate of USDT in rubles at P2P", "Discounts on my wishlist games on Steam",
      "Tickets to Saint Petersburg next Friday: the cheapest price", "Electric train schedule from my station to the city", "Delay of flight SU 1234 today"] }
    : {
    custom: ["Курс золота и его изменение за день", "Доминация биткоина и индекс страха и жадности", "Цена нефти Brent в долларах",
      "Цена бензина АИ-95 на заправках моего города", "Последняя версия Python и дата выхода", "Сколько звёзд у репозитория MemuAI на GitHub",
      "Восход и закат сегодня и долгота дня", "Фаза Луны сегодня", "Индекс качества воздуха (AQI) в моём городе", "Прогноз магнитных бурь на сегодня",
      "Ближайший запуск SpaceX: ракета, миссия и время", "Где сейчас МКС", "Ближайший праздник в России и сколько дней до него",
      "Топ-5 трендовых репозиториев GitHub за сегодня", "Новые модели на Hugging Face за неделю", "Распродажа в Steam: что идёт и когда кончится",
      "Кассовые сборы: топ-5 фильмов выходных", "Премьеры в кино на этой неделе", "Цена iPhone 17 Pro в крупных магазинах",
      "Ставка по ипотеке в Сбере и ВТБ", "Ключевая ставка ЦБ и дата следующего заседания", "Инфляция в России за прошлый месяц",
      "Сколько вакансий Python-разработчика на hh.ru", "Средняя зарплата фронтенд-разработчика", "Пробки в Москве сейчас (баллы)",
      "Результат последней партии матча за шахматную корону", "Формула-1: ближайшая гонка и лидер чемпионата", "UFC: ближайший турнир и главный бой",
      "НБА: результаты прошлой ночи", "Лига чемпионов: матчи ближайшего тура", "Погода на выходные в Сочи",
      "Температура воды в море в Сочи", "Высота снега на курорте Шерегеш", "Прогноз пыльцы для аллергиков",
      "Слово дня из онлайн-словаря", "Этот день в истории (Википедия)", "Самая читаемая статья Википедии сегодня",
      "Топ-5 Hacker News прямо сейчас", "Лучшие запуски Product Hunt за сегодня", "Статус сервисов OpenAI, Anthropic и Google AI",
      "Статус GitHub: есть ли сейчас сбои", "Последнее обновление Windows 11 и что в нём", "Последняя версия драйвера NVIDIA",
      "Дата ближайшей презентации Apple", "Курс USDT в рублях на P2P", "Скидки на игры из моего списка желаемого в Steam",
      "Билеты в Петербург на следующую пятницу: самая низкая цена", "Расписание электричек от моей станции до центра", "Задержка рейса SU 1234 сегодня"] });
  const NEWS_IDEAS = () => (en() ? ["Model releases and open source, no customer stories", "Only official announcements from OpenAI, Anthropic and Google",
    "Research papers that got attention on Hacker News", "Rust and Go ecosystem: releases and notable libraries", "Cybersecurity: actively exploited vulnerabilities",
    "Space launches and missions", "Indie games and major game releases", "Russian tech market: Yandex, Sber, VK", "Apple, Microsoft and Google product launches"]
    : ["Релизы моделей и open-source, без корпоративных кейсов", "Только официальные анонсы OpenAI, Anthropic и Google", "Исследования, которые обсуждают на Hacker News",
      "Экосистема Rust и Go: релизы и заметные библиотеки", "Кибербезопасность: уязвимости, которые уже эксплуатируют", "Космические запуски и миссии",
      "Инди-игры и крупные игровые релизы", "Российский IT-рынок: Яндекс, Сбер, VK", "Новые продукты Apple, Microsoft и Google"]);
  function ideasFor(kind) { return kind === "news" ? NEWS_IDEAS() : IDEAS().custom; }

  function ghostMatch(value, ideas) {
    const q = String(value || "");
    if (!q.trim()) return ideas[0] || "";
    const low = q.toLowerCase();
    const hit = ideas.find((s) => s.toLowerCase().startsWith(low) && s.length > q.length);
    return hit ? hit.slice(q.length) : "";
  }
  function suggestField(kind, name, value, placeholder, required) {
    const ideas = ideasFor(kind);
    const chips = ideas.slice(1, 5);
    return `<div class="mp-field mp-suggest" data-mp-suggest="${esc(kind)}"><span>${esc(kind === "news" ? t("Уточнение или своя тема", "Refine or your own topic") : t("Что показывать", "What to show"))}${required ? " *" : ""}</span>
      <div class="mp-ghost-wrap"><div class="mp-ghost" aria-hidden="true"><span class="typed"></span><span class="rest"></span></div>
        <textarea class="mp-input" name="${esc(name)}" data-ghost rows="2" maxlength="600" aria-describedby="mp-ghost-hint" placeholder="">${esc(value || "")}</textarea></div>
      <small id="mp-ghost-hint" class="mp-ghost-hint"><kbd>Tab</kbd> ${t("— подставить подсказку", "— accept the suggestion")}${placeholder ? " · " + esc(placeholder) : ""}</small>
      <div class="mp-idea-chips" role="list" aria-label="${esc(t("Идеи", "Ideas"))}">${chips.map((c) => `<button type="button" class="mp-idea" role="listitem" data-idea="${esc(c)}">${esc(c)}</button>`).join("")}<button type="button" class="mp-idea more" data-idea-more>${ico("sparkle")}${t("Ещё идеи", "More ideas")}</button></div></div>`;
  }
  function bindSuggest(root) {
    $$q("[data-mp-suggest]", root).forEach((box) => {
      const kind = box.dataset.mpSuggest;
      const area = $q("[data-ghost]", box);
      const typed = $q(".mp-ghost .typed", box), rest = $q(".mp-ghost .rest", box);
      let ideas = ideasFor(kind), page = 1;
      const draw = () => {
        const atEnd = area.selectionStart === area.value.length && area.selectionEnd === area.value.length;
        const tail = document.activeElement === area && !atEnd ? "" : ghostMatch(area.value, ideas);
        typed.textContent = area.value;
        rest.textContent = tail;
        box.dataset.hasGhost = tail ? "1" : "";
        $q(".mp-ghost", box).scrollTop = area.scrollTop;
      };
      const accept = () => {
        const tail = ghostMatch(area.value, ideas);
        if (!tail) return false;
        area.value = area.value + tail;
        area.setSelectionRange(area.value.length, area.value.length);
        area.dispatchEvent(new Event("input", { bubbles: true }));
        return true;
      };
      area.addEventListener("input", draw);
      area.addEventListener("scroll", draw);
      area.addEventListener("keyup", draw);
      area.addEventListener("click", draw);
      area.addEventListener("focus", draw);
      area.addEventListener("blur", draw);
      area.addEventListener("keydown", (event) => {
        if (event.key === "Tab" && !event.shiftKey && !event.ctrlKey && !event.altKey && box.dataset.hasGhost) {
          if (accept()) { event.preventDefault(); draw(); }
        } else if (event.key === "ArrowRight" && box.dataset.hasGhost && area.selectionStart === area.value.length && area.value) {
          if (accept()) { event.preventDefault(); draw(); }
        } else if (event.key === "Escape" && box.dataset.hasGhost && area.value) {
          event.stopPropagation(); rest.textContent = ""; box.dataset.hasGhost = "";
        }
      });
      box.addEventListener("click", (event) => {
        const chip = event.target.closest("[data-idea]");
        if (chip) { area.value = chip.dataset.idea; area.focus(); area.setSelectionRange(area.value.length, area.value.length); area.dispatchEvent(new Event("input", { bubbles: true })); return; }
        if (event.target.closest("[data-idea-more]")) {
          const all = ideasFor(kind);
          const start = 1 + ((page * 4) % Math.max(1, all.length - 1));
          page += 1;
          const pick = all.slice(start, start + 4).concat(all.slice(1, Math.max(1, 5 - (all.length - start))));
          $$q("[data-idea]", box).forEach((b, i) => { if (pick[i]) { b.dataset.idea = pick[i]; b.textContent = pick[i]; } });
          ideas = [all[(start) % all.length], ...all];
          draw();
        }
      });
      draw();
    });
  }

  function openGallery(prefPlace = "") {
    const recent = state.dismissed.slice(0, 6);
    const T = TPLS();
    const groups = GALLERY_GROUPS().map(([title, kinds]) => `<div class="mp-gal-group"><div class="mp-section-title">${esc(title)}</div><div class="mp-gallery">${kinds.filter((k) => T[k]).map((k) => `<button type="button" class="mp-tpl" data-kind="${k}"><span class="mp-tpl-ico">${ico(KIND_ICON[k])}</span><b>${esc(tplName(k)).replace("Производительность", "Производи&shy;тельность")}</b><small>${esc(tplDesc(k))}</small></button>`).join("")}</div></div>`).join("");
    const back = openModal(`${head(t("Новая панель", "New panel"), t("Выберите вид — или попросите в чате: «добавь на главный экран курс доллара».", "Pick a kind — or just ask in chat: “add the dollar rate to my home screen”."))}
      <div class="mp-modal-body">${groups}${window.MemuModInt ? MemuModInt.galleryHtml() : ""}
      ${recent.length ? `<div class="mp-section-title">${t("Недавно убранные", "Recently removed")}</div><div class="mp-dismissed">${recent.map((p) => `<div class="mp-dismissed-row">${ico(KIND_ICON[p.kind] || "dot")}<span>${esc(p.title)}</span><button type="button" class="mp-btn" data-revive="${esc(p.id)}">${t("Вернуть", "Restore")}</button></div>`).join("")}</div>` : ""}</div>`, "wide");
    back.addEventListener("click", (event) => {
      if (event.target.closest("[data-mp-close]")) return closeModal();
      const modPanel = event.target.closest("[data-modpanel-add]");
      if (modPanel) { closeModal(); return MemuModInt.addPanel(modPanel.dataset.modpanelAdd, prefPlace); }
      const tpl = event.target.closest(".mp-tpl");
      if (tpl) return openTemplateForm(tpl.dataset.kind, prefPlace);
      const revive = event.target.closest("[data-revive]");
      if (revive) { closeModal(); act(call(`/api/pg/panels/${revive.dataset.revive}/revive`, { method: "POST" }), t("Панель возвращена", "Panel restored")); }
    });
  }

  function kindNote(kind) {
    return ["sports", "web", "custom"].includes(kind)
      ? t("Эту панель собирает и обновляет модель с веб-поиском: нужен запущенный MemuAI Proxy. Значения берутся только из найденных источников — ссылки видны на карточке.", "A model with web search builds and refreshes this panel, so MemuAI Proxy must be running. Values come only from the sources found — they are linked on the card.")
      : kind === "news" ? t("Лента читает первоисточники и профильные площадки по теме (для мировых тем — на английском), склеивает повторы, отбирает значимое и пишет кратко по-русски. Уже показанное не повторяется. Без модели работает только оценка по источникам.", "The feed reads primary sources and specialised outlets for the topic, merges duplicates, keeps what matters and summarises it. What was shown is not repeated. Without a model only source heuristics are used.")
        : kind === "system" ? t("Всё считается на этом компьютере, в интернет ничего не уходит. Чего Windows не отдаёт без драйверов (температура процессора, вентиляторы), карточка честно покажет как «—».", "Everything is measured on this PC; nothing leaves it. What Windows does not expose without drivers (CPU temperature, fans) is honestly shown as “—”.")
          : kind === "todo" ? t("Задачи хранятся только на этом компьютере, в профиле MemuAI. Быстрый ввод понимает «завтра 18:00», «!1», «#дом», «каждую неделю».", "Tasks are stored only on this PC, in the MemuAI profile. Quick add understands “tomorrow 6pm”, “!1”, “#home”, “every week”.")
            : kind === "calendar" ? t("Нужно подключение Google Calendar в «Настройки → Интеграции». События только читаются.", "Needs Google Calendar connected in Settings → Integrations. Events are only read.")
              : kind === "github" ? t("Нужен токен GitHub в «Настройки → Интеграции». Для уведомлений — classic-токен с правом «notifications».", "Needs a GitHub token in Settings → Integrations. Notifications need a classic token with the “notifications” scope.")
                : kind === "proxy" ? t("Читает статистику MemuAI Proxy с этого компьютера (та же, что на его «Главной»).", "Reads MemuAI Proxy statistics from this PC (the same as on its home page).")
                  : kind === "timers" ? t("Показывает таймеры, будильники, напоминания, секундомер и помодоро «Быстрого доступа» и управляет ими.", "Shows and controls Quick Ask timers, alarms, reminders, the stopwatch and Pomodoro.")
                    : "";
  }
  function sourceFields(kind, params, { creating }) {
    const tpl = TPLS()[kind] || TPLS().custom;
    if (kind === "usage") return accountsEditor((params.accounts || []).map((a) => ({ ...a, has_token: a.mode === "token" })));
    const fields = tpl.fields.filter((f) => !(f.key === "brief" && (kind === "custom" || kind === "news")));
    let html = fields.length === 2 && !fields.some((f) => f.type === "textarea") ? `<div class="mp-field-row">${fields.map((f) => fieldHtml(f, params[f.key])).join("")}</div>` : fields.map((f) => fieldHtml(f, params[f.key])).join("");
    if (kind === "custom" && creating) html += suggestField("custom", "brief", params.brief, "", true);
    if (kind === "news") html += suggestField("news", "brief", params.brief, "", false);
    return html;
  }
  function panelFormHtml(kind, panel, { creating, prefPlace }) {
    const params = panel?.source?.params || {};
    const values = { ...defaultsOf(kind), ...(panel?.options || {}) };
    const ai = panel?.source?.type === "ai";
    const runbook = panel?.runbook || {};
    const main = optionsHtml(kind, values, "main", panel);
    const more = optionsHtml(kind, values, "more", panel);
    const modelKinds = AI_KINDS.includes(kind);
    const aiExtra = ai && !creating ? `<label class="mp-field"><span>${t("Что показывает", "What it shows")}</span><textarea class="mp-input" data-mp-subject>${esc(panel.subject || "")}</textarea></label>
      <label class="mp-field"><span>${t("Как обновлять (инструкция для модели)", "How to refresh (instructions for the model)")}</span><textarea class="mp-input" data-mp-runbook>${esc(runbook.prompt || "")}</textarea></label>
      <label class="mp-field"><span>${t("Страницы-источники", "Source pages")}</span><textarea class="mp-input" data-mp-urls style="min-height:48px" placeholder="https://…">${esc((runbook.urls || []).join("\n"))}</textarea><small>${t("По одной ссылке в строке. Пусто — модель ищет в интернете по запросам.", "One link per line. Empty — the model searches the web.")}</small></label>` : "";
    const note = kindNote(kind);
    const places = panel ? panel.placements || [] : prefPlace ? [prefPlace] : ["home"];
    const model = modelKinds ? modelPicker(panel?.ai_model || "", { inheritLabel: globalModelLabel(), attr: "data-mp-aimodel", label: t("Модель этой панели", "Model for this panel"),
      hint: t("Перекрывает модель из «Настройки → Панели» только для этой панели.", "Overrides the model from Settings → Panels for this panel only.") }) : "";
    return `<form class="mp-form" onsubmit="return false">
        ${creating ? "" : `<label class="mp-field"><span>${t("Название", "Title")}</span><input class="mp-input" name="title" value="${esc(panel.title)}" maxlength="80"></label>`}
        ${sourceFields(kind, params, { creating })}
        ${main ? `<div class="mp-form-group" data-group="show"><div class="mp-group-title">${ico("sliders")}${t("Что показывать", "What to show")}</div>${main}</div>` : ""}
        ${cadenceSelect(panel?.cadence || TPL_CADENCE[kind], kind)}
        ${placeChecks(places)}${sizeSeg(panel?.size || "m", kind)}
        ${more || model || aiExtra ? `<details class="mp-more" data-mp-more><summary>${t("Дополнительно", "Advanced")}</summary><div class="mp-more-body">${model}${more}${aiExtra}</div></details>` : ""}
        ${note ? `<p class="mp-text muted mp-form-note">${esc(note)}</p>` : ""}
        <datalist id="mp-tz-list">${TZ_SUGGEST.map((z) => `<option value="${esc(en() ? z.split("|")[0] + " | " + z.split("|")[0].split("/").pop().replace(/_/g, " ") : z.replace("|", " | "))}"></option>`).join("")}</datalist>
      </form>`;
  }
  function bindPanelForm(back, kind, onChange) {
    pickers(back);
    bindSeg(back); bindGeo(back); bindAccounts(back); newsFieldsSync(back); bindCadence(back); bindSuggest(back);
    const form = $q(".mp-form", back);
    bindOptions(form, kind, onChange);
    back.addEventListener("click", (event) => {
      const b = event.target.closest("[data-mp-size] button");
      if (b) { const hint = $q("[data-size-hint]", back); if (hint) hint.textContent = SIZE_HINTS()[b.dataset.size] || ""; onChange?.(); }
    });

    form.addEventListener("change", (event) => {
      if (!event.target.closest('[data-opt="metrics"]')) return;
      const wrap = $q('[data-opt-wrap="views"]', form);
      const o = (state.schema.system || []).find((x) => x.key === "views");
      if (wrap && o) {
        const vals = readOptions(form);
        const tmp = document.createElement("div");
        tmp.innerHTML = optRow(o, vals.views, "system", { __metrics: vals.metrics });
        wrap.replaceWith(tmp.firstElementChild);
      }
    });
  }

  function openTemplateForm(kind, prefPlace = "") {
    const tpl = TPLS()[kind];
    const back = openModal(`${head(tplName(kind), tplDesc(kind), true)}
      <div class="mp-modal-body">${panelFormHtml(kind, null, { creating: true, prefPlace })}</div>
      <div class="mp-modal-foot"><button type="button" class="mp-btn" data-mp-close>${t("Отмена", "Cancel")}</button><button type="button" class="mp-btn primary" data-mp-create>${ico("plus")}${t("Добавить", "Add")}</button></div>`, "narrow");
    if (kind === "todo" && !state.todoAll) call("/api/pg/panels-todo").then((out) => { state.todoAll = (out.lists || []).map(({ id, name, color }) => ({ id, name, color })); }).catch(() => {});
    bindPanelForm(back, kind);
    back.addEventListener("click", async (event) => {
      if (event.target.closest("[data-mp-close]")) return closeModal();
      if (event.target.closest("[data-mp-back]")) return openGallery(prefPlace);
      if (!event.target.closest("[data-mp-create]")) return;
      const form = $q("form", back);
      const params = kind === "usage" ? {} : readForm(form);
      Object.keys(params).forEach((k) => { if (form.querySelector(`[name="${CSS.escape(k)}"]`)?.closest("[data-opt]")) delete params[k]; });
      const missing = tpl.fields.find((f) => f.required && !params[f.key]);
      if (missing) { notify(t(`Заполните «${missing.ru}»`, `Fill in “${missing.en}”`), "err"); $q(`[name=${missing.key}]`, back)?.focus(); return; }
      if (kind === "news" && !params.topic && !params.brief) { notify(t("Опишите свою тему", "Describe your topic"), "err"); $q("[name=brief]", back)?.focus(); return; }
      const geo = $q("[data-mp-geo]", back);
      if (geo?.dataset.lat && params.city === geo.value.trim()) { params.lat = +geo.dataset.lat; params.lon = +geo.dataset.lon; params.place = params.city; }
      const placements = readPlaces(back);
      if (!placements.length) { notify(t("Выберите, где показывать панель", "Choose where to show the panel"), "err"); return; }
      const button = event.target.closest("[data-mp-create]");
      button.disabled = true;
      button.innerHTML = `<span class="mp-spin"></span>${t("Создаю…", "Creating…")}`;
      try {
        if (kind === "usage") params.accounts = await saveTokens(readAccounts(back));
      } catch (error) { notify(error.message || String(error), "err"); button.disabled = false; button.innerHTML = `${ico("plus")}${t("Добавить", "Add")}`; return; }
      if (kind === "news") params.count = +params.count || 5;
      const options = readOptions(form, kind);
      const body = { template: kind, params, placements, size: readSize(back), cadence: readCadence(back), options,
        subject: kind === "custom" ? params.brief : "", model: typeof currentModel === "function" ? currentModel() : "" };
      const aiModel = $q("[data-mp-aimodel]", back)?.value;
      if (aiModel) body.ai_model = aiModel;
      const out = await act(call("/api/pg/panels", { method: "POST", body }));
      if (!out?.panel) { button.disabled = false; button.innerHTML = `${ico("plus")}${t("Добавить", "Add")}`; return; }
      if (out.panel.status === "building" || out.panel.refreshing) { progressModal(out.panel.id); soon(); }
      else { closeModal(); flash(out.panel.id); notify(out.panel.wall ? wallText(out.panel.wall) : t("Панель добавлена", "Panel added"), out.panel.wall ? "err" : "ok"); }
    });
  }

  function openEditor(id) {
    const panel = byId(id);
    if (!panel) return;
    const kind = panel.kind;
    if (kind === "module") return openModuleEditor(panel);
    const params = panel.source?.params || {};
    const ai = panel.source?.type === "ai";
    const tpl = TPLS()[kind] || TPLS().custom;
    const fields = ai || kind === "usage" ? [] : tpl.fields.filter((f) => !(f.key === "brief" && kind === "custom"));
    const runbook = panel.runbook || {};
    const sub = [tplName(kind), LOCAL_KINDS.includes(kind) ? "" : cadenceLabel(panel.cadence), panel.last_refresh && !LOCAL_KINDS.includes(kind) ? t("обновлено ", "updated ") + ago(panel.last_refresh) : ""].filter(Boolean).join(" · ");
    const back = openModal(`${head(t("Панель", "Panel") + " · " + panel.title, sub)}
      <div class="mp-modal-body"><div class="mp-preview-wrap">${panelFormHtml(kind, panel, { creating: false })}
        <div class="mp-preview-col"><div class="mp-preview-title">${t("Так будет выглядеть", "Preview")}</div><div class="mp-preview" data-mp-preview>${cardHtml(panel, "home", { static: true, size: effectiveSize(panel, "home") })}</div>
          <small class="mp-preview-note" data-preview-note hidden>${t("Новые данные появятся после сохранения и обновления.", "New data appears after saving and refreshing.")}</small></div></div></div>
      <div class="mp-modal-foot"><button type="button" class="mp-btn" data-mp-remove style="margin-right:auto">${ico("trash")}${t("Убрать", "Remove")}</button>
        <button type="button" class="mp-btn" data-mp-close>${t("Отмена", "Cancel")}</button><button type="button" class="mp-btn primary" data-mp-save>${ico("check")}${t("Сохранить", "Save")}</button></div>`, "wide");
    let previewSeq = 0;
    const preview = async () => {
      const form = $q("form", back);
      const options = readOptions(form, kind);
      const size = readSize(back);
      const seq = ++previewSeq;
      let spec = panel.spec, refetch = false;
      if (!ai) {
        try {
          const out = await call("/api/pg/panels-preview", { method: "POST", body: { kind, params, options, before: panel.options || {} } });
          if (seq !== previewSeq) return;
          spec = out.spec || spec; refetch = !!out.refetch;
        } catch (error) {  }
      }
      const fake = { ...panel, spec, options: { ...(panel.options || {}), ...options }, size };
      const box = $q("[data-mp-preview]", back);
      if (box) box.innerHTML = cardHtml(fake, "home", { static: true, size: effectiveSize(fake, "home") });
      const note = $q("[data-preview-note]", back);
      if (note) note.hidden = !refetch;
    };
    let previewT = 0;
    bindPanelForm(back, kind, () => { clearTimeout(previewT); previewT = setTimeout(preview, 160); });
    back.addEventListener("click", async (event) => {
      if (event.target.closest("[data-mp-close]")) return closeModal();
      if (event.target.closest("[data-mp-remove]")) { closeModal(); return remove(id); }
      if (!event.target.closest("[data-mp-save]")) return;
      const form = $q("form", back);
      const values = readForm(form);
      const body = { title: values.title, placements: readPlaces(back), size: readSize(back) };
      const cadence = readCadence(back);
      if (cadence) body.cadence = cadence;
      const options = readOptions(form, kind);
      if (Object.keys(options).length && JSON.stringify(options) !== JSON.stringify(Object.fromEntries(Object.keys(options).map((k) => [k, (panel.options || {})[k]])))) body.options = options;
      const aiModel = $q("[data-mp-aimodel]", back);
      if (aiModel && !aiModel.disabled && aiModel.value !== (panel.ai_model || "")) body.ai_model = aiModel.value;
      try {
        if (kind === "usage") {
          const accounts = await saveTokens(readAccounts(back));
          const before = JSON.stringify((params.accounts || []).map(({ id: i, provider, name, mode, dir }) => ({ id: i, provider, name, mode, dir })));
          if (JSON.stringify(accounts) !== before) body.params = { accounts };
        }
      } catch (error) { notify(error.message || String(error), "err"); return; }
      if (fields.length) {
        const next = {};
        fields.forEach((f) => { next[f.key] = values[f.key] ?? ""; });
        if (kind === "news") next.count = +next.count || 5;
        const geo = $q("[data-mp-geo]", back);
        if (geo) {
          if (geo.dataset.lat && values.city === geo.value.trim()) { next.lat = +geo.dataset.lat; next.lon = +geo.dataset.lon; next.place = values.city; }
          else if (values.city !== String(params.city || "")) { next.lat = null; next.lon = null; next.place = ""; }
        }
        if (kind === "news" && $q("[name=brief]", form)) next.brief = $q("[name=brief]", form).value.trim();
        const changed = Object.keys(next).some((k) => String(next[k] ?? "") !== String(params[k] ?? ""));
        if (changed) body.params = next;
      }
      if (ai) {
        const subject = $q("[data-mp-subject]", back)?.value.trim() ?? panel.subject ?? "";
        const prompt = $q("[data-mp-runbook]", back)?.value.trim() ?? runbook.prompt ?? "";
        const urls = ($q("[data-mp-urls]", back)?.value || "").split(/\s+/).filter((u) => /^https?:\/\//i.test(u));
        if (subject !== (panel.subject || "")) body.subject = subject;
        if (prompt !== (runbook.prompt || "") || urls.join("\n") !== (runbook.urls || []).join("\n")) body.runbook = { ...runbook, prompt, urls };
      }
      const out = await act(call(`/api/pg/panels/${id}`, { method: "PATCH", body }));
      if (out?.panel) { closeModal(); flash(id); if (body.params && kind === "usage") refreshNow(id); }
    });
  }

  async function openModuleEditor(panel) {
    const params = panel.source?.params || {};
    const info = window.MemuModInt ? await MemuModInt.panelInfo(params.module, params.panel) : null;
    const back = openModal(`${head(t("Панель", "Panel") + " · " + panel.title, (info && info.module) || params.module || "")}
      <div class="mp-modal-body"><form class="mp-form" onsubmit="return false">
        <label class="mp-field"><span>${t("Название", "Title")}</span><input name="title" value="${esc(panel.title || "")}" maxlength="80"></label>
        ${sizeSeg(panel.size || "m", "module")}${placeChecks(panel.placements || ["home"])}
        ${info ? info.settingsHtml : `<small class="mp-preview-note">${t("Модуль выключен — включите его в «Настройки → Модули».", "The module is off — turn it on in Settings → Modules.")}</small>`}
      </form></div>
      <div class="mp-modal-foot"><button type="button" class="mp-btn" data-mp-remove style="margin-right:auto">${ico("trash")}${t("Убрать", "Remove")}</button>
        <button type="button" class="mp-btn" data-mp-close>${t("Отмена", "Cancel")}</button><button type="button" class="mp-btn primary" data-mp-save>${ico("check")}${t("Сохранить", "Save")}</button></div>`);
    bindSeg(back);
    back.addEventListener("click", async (event) => {
      if (event.target.closest("[data-mp-close]")) return closeModal();
      if (event.target.closest("[data-mp-remove]")) { closeModal(); return remove(panel.id); }
      if (!event.target.closest("[data-mp-save]")) return;
      const title = ($q("[name=title]", back)?.value || "").trim() || panel.title;
      if (info) { try { await MemuModInt.savePanelSettings(params.module, params.panel, back); } catch (error) { notify(error.message || String(error), "err"); return; } }
      const out = await act(call(`/api/pg/panels/${panel.id}`, { method: "PATCH", body: { title, placements: readPlaces(back), size: readSize(back) } }));
      if (out?.panel) { closeModal(); flash(panel.id); }
    });
  }

  async function locate() {
    const ok = typeof askConfirm === "function" ? await askConfirm(
      t("Определить местоположение?", "Detect your location?"),
      t("MemuAI спросит сервис ipwho.is, в каком городе ваш IP-адрес. Точные координаты устройства не используются; город можно сменить в «Настройки → Панели».",
        "MemuAI will ask ipwho.is which city your IP address is in. Your device's precise position is not used; you can change the city in Settings → Panels."),
      t("Разрешить", "Allow")) : true;
    if (!ok) return;
    const out = await act(call("/api/pg/panels-locate", { method: "POST" }));
    if (out?.settings) notify(t(`Местоположение: ${out.settings.location.name || "определено"}`, `Location: ${out.settings.location.name || "detected"}`));
    else notify(t("Не удалось определить — укажите город вручную", "Could not detect — set the city manually"), "err");
  }
  function cityDialog() {
    const back = openModal(`${head(t("Город для погоды", "City for weather"), t("Используется встроенной панелью погоды и всеми, где город не задан.", "Used by the built-in weather panel and any panel without its own city."))}
      <div class="mp-modal-body"><label class="mp-field"><span>${t("Город", "City")}</span><input class="mp-input" data-mp-geo autocomplete="off" placeholder="${t("Начните вводить…", "Start typing…")}" value="${esc(state.settings.location?.name || "")}"><div class="mp-geo-results"></div></label></div>`, "narrow");
    bindGeo(back);
    $q("[data-mp-geo]", back).addEventListener("mp-geo-pick", (event) => {
      const d = event.detail;
      closeModal();
      act(call("/api/pg/panels-settings", { method: "POST", body: { location: { mode: "manual", name: d.name, lat: +d.lat, lon: +d.lon, country: d.country || "", timezone: d.tz || "" } } }),
        t(`Город: ${d.name}`, `City: ${d.name}`));
    });
    back.addEventListener("click", (event) => { if (event.target.closest("[data-mp-close]")) closeModal(); });
  }

  const zoneOf = (el) => el?.closest?.("[data-mp-zone]") || el?.closest?.("[data-zone-wrap]")?.querySelector("[data-mp-zone]") || null;
  function dropIndex(zone, x, y) {
    const items = $$q(":scope > [data-panel-id]:not(.mp-drag-source):not([hidden])", zone);

    const horizontal = zone.dataset.mpZone === "appbar" || zone.dataset.axis === "x";
    if ((zone.dataset.mpZone === "home" || zone.dataset.flow === "wrap") && items.length) {

      let best = 0, bestD = Infinity;
      items.forEach((el, i) => {
        const r = el.getBoundingClientRect();
        const dx = x < r.left ? r.left - x : x > r.right ? x - r.right : 0;
        const dy = y < r.top ? r.top - y : y > r.bottom ? y - r.bottom : 0;
        const d = dx * dx + dy * dy;
        if (d < bestD) { bestD = d; best = i; }
      });
      const r = items[best].getBoundingClientRect();
      const before = y < r.top + r.height / 2 || (x < r.left && y < r.bottom);
      return before ? best : best + 1;
    }
    for (let i = 0; i < items.length; i += 1) {
      const r = items[i].getBoundingClientRect();
      if (horizontal ? x < r.left + r.width / 2 : (y < r.top || (y < r.bottom && x < r.left + r.width / 2))) return i;
    }
    return items.length;
  }
  function placePlaceholder(zone, index) {
    let ph = state.drag.placeholder;
    if (!ph) {
      ph = document.createElement("div");
      ph.className = "mp-placeholder";
      state.drag.placeholder = ph;
    }
    const panel = byId(state.drag.id);
    ph.dataset.size = zone.dataset.mpZone === "home" ? (panel?.size || "m") : "s";
    if (zone.dataset.mpZone === "home" && panel) {
      ph.dataset.span = String(spanOf(panel));
      const h = state.drag.el?.closest("#panels-home") ? state.drag.el.getBoundingClientRect().height : 112;
      ph.style.minHeight = Math.max(80, Math.min(h, 420)) + "px";
    } else {
      delete ph.dataset.span;
      ph.style.gridRowEnd = "";
      ph.style.minHeight = zone.dataset.mpZone === "sidebar" && zone.dataset.view !== "chips" ? "64px" : "";

      ph.dataset.ph = zone.dataset.ph || "";
      ph.dataset.zsize = panel?.size || "m";
    }
    const items = $$q(":scope > [data-panel-id]:not(.mp-drag-source):not([hidden])", zone);
    const ref = items[index] || $q(":scope > .mp-add-tile, :scope > .mp-side-empty, :scope > .mp-more, :scope > .mp-zone-end", zone);
    if (ph.parentElement !== zone || ph.nextElementSibling !== (ref || null)) {
      zone.insertBefore(ph, ref || null);
      if (zone.dataset.mpZone === "home") layoutGrid();
    }
    state.drag.zone = zone; state.drag.index = index;
  }
  function onDragStart(event) {
    const el = event.target.closest?.("[data-panel-id][draggable=true]");
    if (!el || !zoneOf(el)) return;
    closeMenu(); closePop();
    state.drag = { id: el.dataset.panelId, from: zoneOf(el).dataset.mpZone, el, zone: null, index: -1, placeholder: null };
    event.dataTransfer.effectAllowed = "copyMove";
    try { event.dataTransfer.setData("text/x-memu-panel", el.dataset.panelId); event.dataTransfer.setData("text/plain", byId(el.dataset.panelId)?.title || ""); } catch (e) {  }

    const drag = state.drag;
    requestAnimationFrame(() => { if (state.drag !== drag) return; document.body.classList.add("mp-dragging"); el.classList.add("mp-drag-source"); });
    armDragWatchdog();
  }

  let dragSeen = 0, dragTimer = 0, dragOutside = false;
  function armDragWatchdog() {
    dragSeen = Date.now(); dragOutside = false;
    clearInterval(dragTimer);
    dragTimer = setInterval(() => {
      if (!state.drag) { clearInterval(dragTimer); dragTimer = 0; if (document.body.classList.contains("mp-dragging")) document.body.classList.remove("mp-dragging"); return; }

      if (Date.now() - dragSeen > (dragOutside ? 30000 : 1200)) cleanupDrag();
    }, 400);
  }
  function endStaleDrag(event) {
    if (!state.drag) { if (document.body.classList.contains("mp-dragging")) { document.body.classList.remove("mp-dragging"); queueLayout(); } return; }
    if (event && event.type === "pointermove" && event.buttons) return;
    cleanupDrag();
  }
  function onDragOver(event) {
    if (!state.drag) return;
    dragSeen = Date.now(); dragOutside = false;
    const zone = zoneOf(event.target);
    $$q(".mp-drop-zone.mp-over").forEach((z) => { if (z !== zone) z.classList.remove("mp-over"); });
    LAY()?.autoScroll(event);
    if (!zone) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = event.ctrlKey && zone.dataset.mpZone !== state.drag.from ? "copy" : "move";
    zone.classList.add("mp-over");
    placePlaceholder(zone, dropIndex(zone, event.clientX, event.clientY));
  }
  async function onDrop(event) {
    if (!state.drag) return;
    const zone = zoneOf(event.target) || state.drag.zone;
    if (!zone) return;
    event.preventDefault();
    const { id, from } = state.drag;
    const to = zone.dataset.mpZone;
    const index = state.drag.index;
    const copy = event.ctrlKey && from !== to;
    cleanupDrag();
    const panel = byId(id);
    if (!panel) return;
    LAY()?.remember();
    if (from === to) {
      const ids = placed(to).map((p) => p.id).filter((x) => x !== id);
      ids.splice(Math.max(0, Math.min(index, ids.length)), 0, id);
      ids.forEach((pid, i) => { const p = byId(pid); if (p) p.order = { ...(p.order || {}), [to]: i }; });
      render();
      await act(call("/api/pg/panels-order", { method: "POST", body: { placement: to, ids } }));
    } else {
      const set = new Set(panel.placements || []);
      if (!copy) set.delete(from);
      set.add(to);
      panel.placements = PLACES.filter((p) => set.has(p));
      const ids = placed(to).map((p) => p.id).filter((x) => x !== id);
      ids.splice(Math.max(0, Math.min(index, ids.length)), 0, id);
      ids.forEach((pid, i) => { const p = byId(pid); if (p) p.order = { ...(p.order || {}), [to]: i }; });
      render();
      await act(call("/api/pg/panels-order", { method: "POST", body: { id, from: copy ? "" : from, to, index } }));
      const moved = t(`«${panel.title}» — ${placeName(to).toLowerCase()}`, `“${panel.title}” → ${placeName(to).toLowerCase()}`);

      if (typeof toast === "function" && LAY()) toast(moved, "info", { label: t("Отменить", "Undo"), run: () => LAY().undo() });
      else notify(moved, "info");
    }
    flash(id);
  }
  function cleanupDrag() {
    if (!state.drag) return;
    state.drag.placeholder?.remove();
    state.drag.el?.classList.remove("mp-drag-source");
    $$q(".mp-drop-zone.mp-over").forEach((z) => z.classList.remove("mp-over"));
    document.body.classList.remove("mp-dragging");
    $$q(".mp-drag-source").forEach((x) => x.classList.remove("mp-drag-source"));
    $$q(".mp-placeholder").forEach((x) => x.remove());
    state.drag = null;
    clearInterval(dragTimer); dragTimer = 0;
    queueLayout();
  }

  function noteSave(pid, textValue, now = false) {
    const panel = byId(pid);
    if (panel) panel.note = { text: textValue, updated: Date.now() / 1000 };
    clearTimeout(state.noteTimers?.[pid]);
    state.noteTimers = state.noteTimers || {};
    const run = () => call(`/api/pg/panels-note/${pid}`, { method: "PUT", body: { text: textValue } }).then((out) => { if (panel && out?.note) panel.note = out.note; })
      .catch((error) => notify(t("Заметка не сохранилась: ", "The note was not saved: ") + (error.message || error), "err"));
    if (now) return run();
    state.noteTimers[pid] = setTimeout(run, 600);
    return null;
  }
  function noteEdit(pid, on) {
    const panel = byId(pid);
    if (!panel) return;
    if (on) state.noteEdit[pid] = true; else delete state.noteEdit[pid];
    rerender((p) => p.id === pid);
    if (on) {
      const area = $q(`.mp-card[data-panel-id="${CSS.escape(pid)}"] [data-note-input]`);
      if (area) { area.focus({ preventScroll: true }); area.setSelectionRange(area.value.length, area.value.length); }
    }
  }
  function bindNotes() {
    document.addEventListener("click", (event) => {
      const view = event.target.closest("[data-note-edit]");
      if (!view || event.target.closest("[data-note-line], label")) return;
      const pid = view.closest("[data-panel-id]")?.dataset.panelId;
      if (pid) noteEdit(pid, true);
    });
    document.addEventListener("keydown", (event) => {
      const view = event.target.closest?.("[data-note-edit]");
      if (view && (event.key === "Enter" || event.key === "F2")) { event.preventDefault(); const pid = view.closest("[data-panel-id]")?.dataset.panelId; if (pid) noteEdit(pid, true); return; }
      const area = event.target.closest?.("[data-note-input]");
      if (area && event.key === "Escape") { event.preventDefault(); event.stopPropagation(); const pid = area.closest("[data-panel-id]").dataset.panelId; noteSave(pid, area.value, true); noteEdit(pid, false); }
    });
    document.addEventListener("input", (event) => {
      const area = event.target.closest?.("[data-note-input]");
      if (area) { const pid = area.closest("[data-panel-id]")?.dataset.panelId; if (pid) noteSave(pid, area.value); return; }
      const add = event.target.closest?.("[data-todo-add]");
      if (add) { const pid = add.closest("[data-panel-id]")?.dataset.panelId; if (pid) todoUi(pid).draft = add.value; }
    });
    document.addEventListener("focusout", (event) => {
      const area = event.target.closest?.("[data-note-input]");
      if (!area) return;
      const pid = area.closest("[data-panel-id]")?.dataset.panelId;
      if (!pid) return;
      setTimeout(() => {
        if (document.activeElement?.closest?.("[data-note-input]")) return;
        if (state.noteEdit[pid]) { noteSave(pid, area.value, true); noteEdit(pid, false); }
      }, 120);
    });
    document.addEventListener("change", (event) => {
      const box = event.target.closest?.("[data-note-line]");
      if (!box) return;
      const pid = box.closest("[data-panel-id]")?.dataset.panelId;
      const panel = byId(pid);
      if (!panel) return;
      const lines = String(panel.note?.text || "").split("\n");
      const i = +box.dataset.noteLine;
      if (lines[i] != null) lines[i] = lines[i].replace(/\[( |x|х)\]/i, box.checked ? "[x]" : "[ ]");
      noteSave(pid, lines.join("\n"), true);
      rerender((p) => p.id === pid);
    });
  }

  function bindEvents() {
    document.addEventListener("click", (event) => {
      const target = event.target;
      if (state.menu && !state.menu.handle && !target.closest(".mp-menu") && !target.closest("[data-mp-menu]")) closeMenu();
      if (state.pop && !target.closest(".mp-pop") && !target.closest(".mp-chip") && !target.closest("[data-mp-list]")) closePop();
      const menuBtn = target.closest("[data-mp-menu]");
      if (menuBtn) {
        event.preventDefault(); event.stopPropagation();
        const id = menuBtn.closest("[data-panel-id]")?.dataset.panelId;
        const justClosed = state.menuClosed && state.menuClosed.id === id && state.menuClosed.reason === "outside" && performance.now() - state.menuClosed.at < 400;
        if (state.menu?.id === id) closeMenu(); else if (!justClosed) openMenu(id, menuBtn);
        return;
      }
      const actBtn = target.closest("[data-mp-act]");
      if (actBtn) {
        const id = actBtn.closest("[data-panel-id]")?.dataset.panelId;
        const a = actBtn.dataset.mpAct;
        if (a === "gallery") openGallery(actBtn.closest("[data-mp-zone]")?.dataset.mpZone || "");
        else if (a === "refresh" && id) refreshNow(id);
        else if (a === "connect") openIntegrations(actBtn.dataset.mpConnector || "");
        else if (a === "locate") locate();
        else if (a === "city") cityDialog();
        else if (a === "more") openMore(actBtn);
        else if (a === "edit" && id) openEditor(id);
        else if (a === "arrange") LAY()?.enter();
        return;
      }
      const listBtn = target.closest("[data-mp-list]");
      if (listBtn) {
        event.preventDefault(); event.stopPropagation();
        const id = listBtn.closest("[data-panel-id]")?.dataset.panelId;
        if (id) { if (state.pop?.id === "__list:" + id) closePop(); else openListPop(id, +listBtn.dataset.mpList, listBtn); }
        return;
      }
      if (target.closest("[data-mp-pop-close]")) { closePop(); return; }
      const ask = target.closest("[data-mp-ask]");
      if (ask) { closePop(); askInChat(ask.dataset.mpAsk); return; }
      if (target.closest("#btn-panel-new")) { openGallery("sidebar"); return; }
      const pin = target.closest("[data-panel-pin]");
      if (pin) {
        const id = pin.closest("[data-panel-inline]")?.dataset.panelInline;
        if (id) {
          const panel = byId(id);
          if (panel?.dismissed_at) act(call(`/api/pg/panels/${id}/revive`, { method: "POST" }));
          else togglePlace(id, pin.dataset.panelPin);
        }
        return;
      }
      const chip = target.closest(".mp-chip[data-panel-id][data-place], .mp-pop-list .mp-chip[data-panel-id]");
      if (chip) {
        const anchor = chip.closest(".mp-pop-list") ? (state.pop?.anchor || chip) : chip;
        if (chip.closest(".mp-pop-list")) closePop();
        openPop(chip.dataset.panelId, anchor);
      }
    });

    let lastDown = null;
    document.addEventListener("pointerdown", (event) => {
      const el = event.target.closest?.("[data-panel-id][data-place]");
      lastDown = el ? { el, x: event.clientX, y: event.clientY, at: performance.now() } : null;
    }, true);
    const cardAt = (event) => {
      let el = event.target?.isConnected ? event.target.closest?.("[data-panel-id][data-place]") : null;
      if (!el && lastDown && lastDown.el.isConnected && performance.now() - lastDown.at < 3000 && Math.abs(lastDown.x - event.clientX) + Math.abs(lastDown.y - event.clientY) < 12) el = lastDown.el;
      if (!el && !event.target?.isConnected) el = document.elementFromPoint(event.clientX, event.clientY)?.closest?.("[data-panel-id][data-place]") || null;
      return el;
    };
    document.addEventListener("contextmenu", (event) => {
      if (EMBED) return;
      const el = cardAt(event);
      if (!el || el.closest(".mp-modal") || el.closest(".mp-pop") || el.closest("#messages")) return;
      if (event.target.isConnected && event.target.closest?.("a[href], input, textarea, [contenteditable]")) return;
      event.preventDefault();
      event.stopPropagation();
      openMenu(el.dataset.panelId, el, { x: event.clientX, y: event.clientY });
    }, true);
    document.addEventListener("keydown", (event) => {
      const el = document.activeElement?.closest?.("[data-panel-id][data-place]");
      if (event.key === "Escape") { closeMenu(); closePop(); }
      if (!el) return;

      if (document.activeElement !== el && document.activeElement?.closest?.("input, textarea, select, [contenteditable], .mp-todo, .mp-note, .mp-timers")) return;
      if ((event.key === "F10" && event.shiftKey) || event.key === "ContextMenu") { event.preventDefault(); openMenu(el.dataset.panelId, el, null, true); }
      if (event.key === "Delete" && !EMBED && !el.closest(".mp-pop")) { event.preventDefault(); remove(el.dataset.panelId); }
    });

    document.addEventListener("mousedown", (event) => {
      const card = event.target.closest?.(".mp-card[draggable=true]");
      if (!card || !event.target.closest("input, textarea, select, button, label, .mp-note-view, .mp-todo-item")) return;
      card.setAttribute("draggable", "false");
      const back = () => { card.setAttribute("draggable", "true"); document.removeEventListener("mouseup", back, true); };
      document.addEventListener("mouseup", back, true);
    }, true);
    bindTodo(); bindNotes();
    document.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-tm]");
      if (btn && btn.closest(".mp-timers")) { event.stopPropagation(); timerAct(btn); }
    });
    document.addEventListener("dragstart", onDragStart);
    document.addEventListener("dragover", onDragOver);
    document.addEventListener("drop", onDrop);
    document.addEventListener("dragend", cleanupDrag);

    document.addEventListener("drop", () => setTimeout(() => { if (state.drag) cleanupDrag(); }, 0));
    document.addEventListener("dragleave", (e) => { if (state.drag && !e.relatedTarget) { dragOutside = true; dragSeen = Date.now(); } });
    document.addEventListener("pointerup", endStaleDrag, true);
    document.addEventListener("pointermove", endStaleDrag, true);
    document.addEventListener("mouseup", endStaleDrag, true);
    window.addEventListener("blur", () => { if (state.drag) setTimeout(() => { if (state.drag && !dragOutside && Date.now() - dragSeen > 1200) cleanupDrag(); }, 1300); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && (state.drag || document.body.classList.contains("mp-dragging"))) endStaleDrag(null); }, true);
    window.addEventListener("focus", () => load());
    document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); syncLive(); });
    window.addEventListener("resize", () => { closeMenu(); fitAppbar(); if (state.pop) closePop(); });
    if (typeof ResizeObserver === "function") {
      let raf = 0;
      const ro = new ResizeObserver(() => { cancelAnimationFrame(raf); raf = requestAnimationFrame(fitAppbar); });

      const attach = () => { const bar = $q("#panels-appbar"); if (bar) ro.observe(bar); else setTimeout(attach, 500); };
      attach();
    }

    const liveCd = document.getElementsByClassName("mp-countdown"), liveClock = document.getElementsByClassName("mp-clock"),
      liveChip = document.getElementsByClassName("mp-chip");
    setInterval(() => {
      if (document.hidden) return;
      if (!liveCd.length && !liveClock.length && !liveChip.length && !state.timers?.snap) return;
      Array.prototype.filter.call(liveCd, (el) => el.hasAttribute("data-mp-countdown")).forEach((el) => {
        let opts = {};
        try { opts = JSON.parse(el.dataset.mpCd || "{}"); } catch (e) { opts = {}; }
        const html = countdownCells(el.dataset.mpCountdown, el.dataset.size, opts);
        if (el.innerHTML !== html) el.innerHTML = html;
      });

      const minute = String(Math.floor(Date.now() / 60000));
      Array.prototype.filter.call(liveClock, (el) => el.hasAttribute("data-mp-clock")).forEach((el) => {
        let b = {};
        try { b = JSON.parse(el.dataset.mpClock || "{}"); } catch (e) { b = {}; }
        if (!b.seconds && el.dataset.drawn === minute) return;
        el.dataset.drawn = minute;
        el.innerHTML = clockInner(b, el.dataset.size, el.dataset.mode);
      });
      Array.prototype.filter.call(liveChip, (el) => el.dataset.panelId && el.dataset.place).forEach((chip) => {
        const panel = byId(chip.dataset.panelId);
        if (panel && (panel.kind === "clock" || panel.kind === "timers")) { const v = chip.querySelector(".mp-chip-value"); const m = miniOf(panel); if (v && m.value && v.textContent !== m.value) v.textContent = m.value; }
      });
      tickTimers();
    }, 1000);
    setInterval(() => {
      if (document.hidden) return;
      $$q("[data-mp-ago]").forEach((el) => { el.textContent = ago(+el.dataset.mpAgo); });
      $$q("[data-mp-reset]").forEach((el) => {
        const ts = +el.dataset.mpReset;
        el.innerHTML = `${esc(t("сброс через ", "resets in ") + untilText(ts))}<span class="sep">·</span>${esc(clockText(ts))}`;
      });
    }, 15000);

    if (!EMBED) document.addEventListener("memu:lang", () => { if (state.loaded) { render(); syncLang(); } });
    if (!EMBED) setInterval(() => {
      if (!state.loaded) return;
      if (!$q("#panels-home") || !$q("#group-panels") || !$q("#panels-appbar")) render();
      const label = $q("#group-panels [data-mp-i18n=panels]");
      if (label && label.textContent !== t("Панели", "Panels")) { render(); syncLang(); }
    }, 2000);
  }

  function summary(tool, result) {
    soon();
    setTimeout(() => load(true), 300);
    const p = result?.panel;
    if (tool === "panel_list") return t(`${(result.panels || []).length} панелей`, `${(result.panels || []).length} panels`);
    if (tool === "panel_remove") return t("убрана", "removed") + (result.removed ? ` · #${result.removed}` : "");
    if (!p) return "";
    const status = p.status === "building" ? t("собирается", "building") : p.wall ? t("есть препятствие", "blocked") : t("готова", "ready");
    return `«${p.title}» · ${status} · #${p.id}`;
  }
  function inlineIds(m) {
    const ids = [];
    for (const row of m?.toolHistory || []) {
      if (!row || !row.ok || !/^panel_(create|edit|revive|refresh|update_data)$/.test(String(row.tool))) continue;
      const match = String(row.summary || "").match(/#(p_[a-z0-9]{6,24})/);
      if (match && !ids.includes(match[1])) ids.push(match[1]);
    }
    return ids.slice(0, 3);
  }
  function inlineInner(id) {
    const panel = byId(id);
    if (!panel) {
      if (!state.loaded) return `<div class="mp-card" data-mode="inline" data-status="building"><div class="mp-skel"><i></i><i></i><i></i></div></div>`;
      return `<div class="mp-inline-gone">${t("Панель удалена.", "The panel was deleted.")}</div>`;
    }
    if (panel.dismissed_at) {
      return `<div class="mp-inline-gone">${t(`Панель «${esc(panel.title)}» убрана.`, `Panel “${esc(panel.title)}” was removed.`)} <button type="button" class="mp-pin" data-panel-pin="home">${ico("refresh")}${t("Вернуть", "Restore")}</button></div>`;
    }
    const places = new Set(panel.placements || []);
    return cardHtml(panel, "inline", { static: true, size: "m", place: "inline" })
      + `<div class="mp-pins"><span>${t("Закрепить:", "Pin to:")}</span>${PLACES.filter((p) => places.has(p) || ["home", "sidebar", "appbar"].includes(p)).map((p) => `<button type="button" class="mp-pin" data-panel-pin="${p}" aria-pressed="${places.has(p)}">${ico(places.has(p) ? "check" : placeIcon(p))}${placeName(p)}</button>`).join("")}</div>`;
  }
  function inlineHtml(m) {
    if (!m || m.role !== "assistant" || !m.toolHistory?.length) return "";
    const ids = inlineIds(m);
    if (!ids.length) return "";
    if (!state.loaded) load();
    return ids.map((id) => `<div class="mp-inline" data-panel-inline="${esc(id)}">${inlineInner(id)}</div>`).join("");
  }
  function refreshInline() {
    $$q("#messages [data-panel-inline]").forEach((el) => {
      const html = inlineInner(el.dataset.panelInline);
      if (el.innerHTML !== html) el.innerHTML = html;
    });
    refreshPop();
  }

  const SKILL_ID = "memuai_panels";
  const SKILL_PROMPT = [
    "PURPOSE: create and manage MemuAI panels — small glanceable cards the user keeps on the start screen (home), in the left sidebar (sidebar) or as a mini chip in the top bar (appbar). A panel is a spec of blocks bound to data with {{key}}, plus how to re-gather the values (a built-in source or a runbook for a model with web search) and a time-based cadence.",
    "",
    "WHEN",
    "The user asks for a panel, widget, tile, card, tracker, dashboard, ticker or countdown on the home screen, sidebar or top bar (\"keep the dollar rate on my home screen\", \"show Zenit's next game in the sidebar\"), or to change, refresh, move or remove one. A one-off question (\"what is the rate now?\") is answered normally, not with a panel.",
    "",
    "TOOLS (the same hidden memuai-tool JSON block as other tools; write nothing for the user in that turn)",
    "- panel_create {subject, rhythm, placement: \"home\"|\"sidebar\"|\"appbar\" (comma-separated for several), size: \"s\"|\"m\"|\"l\", template?, params?, title?, blocks?, mini?, runbook?: {prompt, queries:[…], urls:[…]}, cadence?: {kind:\"interval\",minutes} | {kind:\"daily\",at:\"HH:MM\"} | {kind:\"manual\"}, values?, sources?}",
    "- panel_list {} · panel_read {id} · panel_refresh {id} · panel_remove {id} · panel_revive {id}",
    "- panel_update_data {id, values, sources?} — new values only (keys the spec binds) · panel_edit {id, title?, blocks?, mini?, placements?, size?, cadence?, runbook?, subject?, params?} — structure and settings",
    "",
    "BUILT-IN TEMPLATES (refresh from a direct source, no model; prefer them when they fit):",
    "weather {city?} (empty city = the user's location from Settings → Panels) · currency {base, quote} ISO codes (RUB uses the Bank of Russia rate) · crypto {coin: CoinGecko id like bitcoin, ethereum, the-open-network, vs: usd|eur|rub} · stock {symbol, exchange: moex|us} · rss {url, title?} · countdown {title, target: ISO local datetime} · sports {team} · web {url, what}",
    "news {topic: ai|tech|science|crypto|security|space|gaming, brief?: the user's own topic or refinement, count: 3-10} — a curated feed: primary sources (official blogs, Hacker News, Reddit, specialised outlets; English-first for global topics), duplicates merged, ranked by significance and credibility, never repeats what it already showed. ANY request for news/headlines/releases/digest uses this template, never custom blocks.",
    "system {} — local PC performance (CPU, GPU, RAM, disks, network, temperatures, battery), live while on screen · usage {} — local Claude / Codex / Antigravity limits, or Kiro / other existing MemuAI Proxy accounts, API balances and quota planner snapshots; the user selects accounts in the card's editor, so create it with no params and tell them to select accounts there.",
    "todo {} — the user's own task lists (stored locally; quick add, due dates, priorities, tags, subtasks, repeats, reminders) · notes {} — a local note/checklist · clock {} with options.zones [\"Europe/London|Лондон\", …] · timers {} — Quick Ask Pomodoro, timers and stopwatch · proxy {} — MemuAI Proxy statistics · calendar {} — upcoming Google Calendar events (needs the integration) · github {} — GitHub notifications / review requests (needs the integration).",
    "OPTIONS: panel_create and panel_edit accept options {…} that choose what a built-in panel shows, e.g. system {metrics:[cpu,cores,ram,swap,gpu,vram,gpu_temp,disks,disk_io,net,temps,fans,battery,uptime,procs], style: rings|bars|numbers|charts, live: off|1|2|5}, weather {fields:[feels,hilo,wind,humidity,precip,pressure,uv,sun,visibility], forecast: auto|none|hourly|daily|both, days, hours}, currency/crypto/stock {extra:[codes], period}, news {hours, summary, sources, feeds, exclude}, rss {count, extra, include, exclude}, todo {filter, sort, show_done}. panel_templates lists every option. Sizes: s (¼ width), m (½), l (½, all details), w (full width).",
    "",
    "RULES",
    "1. Show only what the user asked for — no padding blocks.",
    "2. Never invent a live value. Pass values only if a tool result in this conversation contained them (with sources); otherwise omit values — a background run gathers them from the runbook and cites its sources on the card.",
    "3. For anything without a template give subject + blocks bound to {{keys}} + runbook (search queries and/or the URLs that hold the values). Without blocks the background run designs the panel itself from the subject.",
    "4. Cadence: the slowest honest pace. Allowed: interval {minutes, days?:[0-6 Mon..Sun], from?:\"HH:MM\", to?:\"HH:MM\"}, {kind:\"times\", at:[\"08:00\",\"19:00\"], days?}, daily at HH:MM, or manual — but each type has a floor (AI/web/news panels ≥ 15 min, weather and currency ≥ 15, rss ≥ 5, system/usage/crypto/stock ≥ 1). Content scoped to a day is daily; a countdown needs no refresh. Panels refresh by time only — they cannot fire on events: \"tell me when X happens\" becomes a scheduled check at an honest cadence (or a Planning task), and say so.",
    "5. Call panel_create once per request. The tool waits for the first refresh: if its note says the panel is ready, say so in one sentence; if it is still building, say it is on its way; if it hit a wall, say plainly what blocks it. The chat shows a live preview with pin buttons — do not describe its layout or repeat its values.",
    "6. Use the ids from <current_panels> (or panel_list) to edit, refresh or remove; a removed panel can be brought back with panel_revive.",
    "7. A log of things only the user would type (meals, workouts) cannot be a panel: panels show information read from somewhere else — say so plainly and offer a panel from a source instead.",
    "",
    "BLOCKS: title {text, subtitle, icon} · metric {value, unit, label, delta, trend: up|down|flat, caption, icon} · stats {items:[{label, value, unit}]} · list {items:[{title, subtitle, value, url}] or \"{{key}}\", max, label} · strip {items:[{label, value, sub, icon}]} · progress {value, max, label, caption} · sparkline {points:[numbers] or \"{{key}}\", label} · countdown {target} · badge {text, tone: ok|warn|danger|info|neutral|accent} · text {text, muted} · link {text, url} · action {text, prompt} (asks MemuAI the prompt) · image {src: https, alt} · row {blocks:[≤4]}. Optional min_size: \"m\"|\"l\" hides a block on smaller cards. mini {icon, value, unit, label} is the top-bar chip. Icons: clear, partly, cloudy, rain, snow, storm, chart, coin, btc, news, trophy, calendar, clock, globe, bolt, heart, star, bell, home, cart, book, music, film, plane, car, fire, drop, wind, sun, moon, pin, flag, user, mail, gift.",
    "",
    "EXAMPLE",
    "```memuai-tool",
    "[{\"tool\":\"panel_create\",\"args\":{\"subject\":\"Next home game of FC Zenit and the latest score\",\"rhythm\":\"changes after each match\",\"placement\":\"sidebar\",\"size\":\"m\",\"title\":\"Зенит\",\"blocks\":[{\"type\":\"title\",\"text\":\"Зенит\",\"subtitle\":\"{{competition}}\",\"icon\":\"trophy\"},{\"type\":\"metric\",\"value\":\"{{last_score}}\",\"label\":\"{{last_match}}\"},{\"type\":\"text\",\"text\":\"{{next_match}}\",\"muted\":true}],\"mini\":{\"icon\":\"trophy\",\"value\":\"{{last_score}}\"},\"runbook\":{\"prompt\":\"Find Zenit's latest finished match (score, opponent) and the next match (date, opponent).\",\"queries\":[\"Зенит последний матч счёт\",\"Зенит следующий матч\"]},\"cadence\":{\"kind\":\"interval\",\"minutes\":180}}}]",
    "```",
  ].join("\n");
  function registerSkill() {
    const lib = window.MEMUAI_SKILL_LIBRARY;
    if (!lib || !Array.isArray(lib.skills) || lib.skills.some((s) => s.id === SKILL_ID)) return;
    lib.skills.push({
      id: SKILL_ID, name: "Panels", name_ru: "Панели", category: "MemuAI", category_ru: "MemuAI",
      desc: "Creates and manages panels: weather, rates, headlines, scores and anything else as cards on the home screen, in the sidebar or the top bar.",
      desc_ru: "Создаёт панели и управляет ими: погода, курсы, новости, счёт матчей и что угодно ещё — карточками на главном экране, в боковой или верхней панели.",
      when: "The user asks to put something on the home screen, sidebar or top bar as a panel/widget/tracker, or to change, refresh or remove a panel.",
      when_ru: "Просьба вывести что-то на главный экран, в боковую или верхнюю панель как панель/виджет/трекер, либо изменить, обновить или убрать панель.",
      inline: true, removable: true, prompt: SKILL_PROMPT,
    });
  }
  function enabled() {
    try { return typeof getSkills === "function" && getSkills().some((s) => s.id === SKILL_ID && s.enabled); } catch (e) { return false; }
  }

  function systemBlock(mode = "") {
    if (mode === "work" || mode === "code" || !enabled()) return "";
    const skill = getSkills().find((s) => s.id === SKILL_ID);
    const rows = state.panels.slice(0, 20).map((p) => `${p.id} — ${p.title} (${p.kind}; ${(p.placements || []).join(", ") || "not pinned"}${p.wall ? "; wall " + p.wall.status : ""})`).join("\n");
    return `<panels_skill>\n${String(skill?.prompt || SKILL_PROMPT).trim()}\n\n<current_panels>\n${rows || "(none)"}\n</current_panels>\n</panels_skill>`;
  }

  function mountSettings() {
    const pane = $q("#pane-panels");
    if (!pane) return;
    if (!$q(".mp-settings", pane)) pane.innerHTML = '<div class="mp-settings"></div>';
    renderSettings();
    if (!state.loaded) load();
  }
  function renderSettings() {
    const box = $q("#pane-panels .mp-settings");
    if (!box) return;
    const s = state.settings || {};
    const loc = s.location || {};
    const locText = loc.mode === "manual" ? `${loc.name || "—"} · ${t("задан вручную", "set manually")}`
      : loc.mode === "auto" ? `${loc.name || t("определено", "detected")} · ${t("по IP-адресу", "by IP address")}` : t("не задано", "not set");
    const models = (typeof S !== "undefined" && Array.isArray(S.models) ? S.models : [])
      .filter((m) => !(typeof isImageModel === "function" && isImageModel(m.id)) && !(typeof isMusicOnlyModel === "function" && isMusicOnlyModel(m.id)));
    const name = (id) => (typeof modelDisplayName === "function" ? modelDisplayName(id) : id);
    const scroll = box.closest(".settings-body")?.scrollTop;

    const K = window.MemuSettingsKit;
    const section = (o) => (K ? K.section(o) : `<section class="sx-section"><h3>${o.title || ""}</h3><div class="sx-card">${o.body}</div></section>`);
    const row = (o) => (K ? K.row(o) : `<div class="sx-row"><div class="sx-row-text"><div class="sx-label">${o.label}</div>${o.desc ? `<div class="sx-desc">${o.desc}</div>` : ""}</div><div class="sx-control">${o.control || ""}</div></div>`);
    const list = state.panels.length ? state.panels.map((p) => `<div class="mp-set-item" data-panel-id="${esc(p.id)}">
          <span class="mp-set-ico">${ico(p.kind === "weather" && p.data?.icon ? p.data.icon : (KIND_ICON[p.kind] || "dot"))}</span>
          <div class="t"><b>${esc(p.title)}</b><small>${esc(tplName(p.kind))} · ${esc(cadenceLabel(p.cadence))}${p.last_refresh ? " · " + esc(ago(p.last_refresh)) : ""}${p.wall ? " · " + esc(wallText(p.wall, p)) : ""}</small></div>
          <span class="mp-set-acts">${(p.placements || []).length ? `<span class="mp-set-places">${(p.placements || []).map((pl) => esc(placeName(pl))).join(" · ")}</span>` : ""}
          <button type="button" class="mp-x" data-set-edit title="${t("Изменить", "Edit")}" aria-label="${t("Изменить", "Edit")}: ${esc(p.title)}">${ico("edit")}</button><button type="button" class="mp-x" data-set-remove title="${t("Убрать", "Remove")}" aria-label="${t("Убрать", "Remove")}: ${esc(p.title)}">${ico("trash")}</button></span></div>`).join("")
      : `<div class="mp-set-empty"><b>${t("Панелей пока нет", "No panels yet")}</b><span>${t("Добавьте из галереи или попросите в чате — ИИ соберёт любую.", "Add one from the gallery or ask in chat — the AI builds any panel.")}</span></div>`;
    const dismissed = state.dismissed.length ? `<details class="mp-set-dismissed"><summary>${t("Недавно убранные", "Recently removed")} <span>${state.dismissed.length}</span></summary><div class="mp-dismissed">${state.dismissed.map((p) => `<div class="mp-dismissed-row">${ico(KIND_ICON[p.kind] || "dot")}<span>${esc(p.title)}</span><button type="button" class="mp-btn" data-revive="${esc(p.id)}">${t("Вернуть", "Restore")}</button><button type="button" class="mp-x" data-purge="${esc(p.id)}" title="${t("Удалить навсегда", "Delete forever")}" aria-label="${t("Удалить навсегда", "Delete forever")}">${ico("close")}</button></div>`).join("")}</div></details>` : "";
    box.innerHTML = section({ id: "mp-set-panels", title: t("Ваши панели", "Your panels"),
        desc: t("Погода, курсы, новости, счёт, отсчёт — над и под полем ввода, в колонках, сбоку или значком сверху. «Расположение» — перетаскивание и размеры.", "Weather, rates, news, scores, countdowns — above and below the message box, in columns, at the side or as a chip at the top. «Layout» — drag and sizes."),
        aside: `${LAY() ? `<button type="button" class="mp-btn" data-mp-act="arrange" title="${t("Перетаскивайте панели между местами окна, меняйте размер и вид мест", "Drag panels between the places of the window, change sizes and the look of places")}">${ico("arrange")}${t("Расположение", "Layout")}</button>` : ""}<button type="button" class="mp-btn primary" data-mp-act="gallery">${ico("plus")}${t("Добавить панель", "Add a panel")}</button>`,
        body: `<div class="mp-set-list">${list}</div>${dismissed}` })
      + section({ id: "mp-set-weather", title: t("Погода", "Weather"), body:
        row({ label: t("Местоположение для погоды", "Location for weather"), desc: `${esc(locText)} · ${t("по IP — только с вашего согласия (ipwho.is), погода — Open-Meteo без ключей", "by IP only with your consent (ipwho.is), weather from Open-Meteo, no keys")}`,
          control: `<span class="mp-setup-row"><button type="button" class="mp-btn" data-mp-act="locate">${ico("locate")}${t("По IP", "By IP")}</button>
            <button type="button" class="mp-btn" data-mp-act="city">${ico("search")}${t("Город…", "City…")}</button>
            ${loc.mode !== "unset" ? `<button type="button" class="mp-btn ghost" data-set-loc-reset>${t("Сбросить", "Reset")}</button>` : ""}</span>` })
        + row({ label: t("Единицы", "Units"), control: `<div class="mp-seg" style="margin:0" data-mp-seg="units">${[["auto", t("Как в системе", "System")], ["metric", t("°C, м/с", "°C, m/s")], ["imperial", "°F, mph"]].map(([v, l]) => `<button type="button" data-units="${v}" aria-pressed="${(s.units || "auto") === v}">${esc(l)}</button>`).join("")}</div>` }) })
      + section({ id: "mp-set-refresh", title: t("Обновление", "Refreshing"), body:
        row({ label: t("Обновлять автоматически", "Refresh automatically"),
          desc: t("Пока открыта MemuAI: от раза в минуту (ПК, лимиты, курсы) до раза в день. Если источник просит реже — панель ждёт.", "While MemuAI is open: from every minute (PC, limits, prices) to daily. When a source asks to slow down, the panel waits."),
          control: `<label class="switch sx-switch"><input type="checkbox" role="switch" data-set-enabled${s.enabled !== false ? " checked" : ""} aria-label="${t("Обновлять автоматически", "Refresh automatically")}"><span class="sl"></span></label>` })
        + `<div class="sx-row sx-row--stack mp-set-model">${modelPicker(s.model || "", { inheritLabel: t("Основная модель MemuAI", "The MemuAI default model"), attr: "data-set-model", label: t("Модель для панелей с ИИ", "Model for AI panels"),
          hint: t("Новости, свои панели, спорт и значения со страниц — через MemuAI Proxy, не чаще раза в 15 минут. У панели может быть своя модель: «Настроить… → Дополнительно».", "News, custom panels, sports and page values — through MemuAI Proxy, at most every 15 minutes. A panel can have its own model: “Customize… → Advanced”.") })}</div>`
        + row({ label: t("Мини-панелей в верхней панели", "Mini panels in the top bar"), control: `<select class="mp-input" data-set-appbar style="width:90px">${[1, 2, 3, 4, 5, 6, 8].map((n) => `<option value="${n}"${+s.appbar_max === n ? " selected" : ""}>${n}</option>`).join("")}</select>` }) });
    if (scroll != null) box.closest(".settings-body").scrollTop = scroll;
  }
  function bindSettings() {
    document.addEventListener("click", (event) => {
      const box = event.target.closest("#pane-panels .mp-settings");
      if (!box) return;
      const item = event.target.closest(".mp-set-item");
      if (event.target.closest("[data-set-edit]") && item) return openEditor(item.dataset.panelId);
      if (event.target.closest("[data-set-remove]") && item) return remove(item.dataset.panelId);
      const revive = event.target.closest("[data-revive]");
      if (revive) return act(call(`/api/pg/panels/${revive.dataset.revive}/revive`, { method: "POST" }), t("Панель возвращена", "Panel restored"));
      const purge = event.target.closest("[data-purge]");
      if (purge) return act(call(`/api/pg/panels/${purge.dataset.purge}?purge=1`, { method: "DELETE" }));
      const units = event.target.closest("[data-units]");
      if (units) return act(call("/api/pg/panels-settings", { method: "POST", body: { units: units.dataset.units } }));
      if (event.target.closest("[data-set-loc-reset]")) return act(call("/api/pg/panels-settings", { method: "POST", body: { location: { mode: "unset", name: "", lat: null, lon: null } } }));
    });
    document.addEventListener("change", (event) => {
      if (!event.target.closest("#pane-panels .mp-settings")) return;
      if (event.target.matches("[data-set-enabled]")) act(call("/api/pg/panels-settings", { method: "POST", body: { enabled: event.target.checked } }));
      if (event.target.matches("[data-set-model]")) act(call("/api/pg/panels-settings", { method: "POST", body: { model: event.target.value } }));
      if (event.target.matches("[data-set-appbar]")) act(call("/api/pg/panels-settings", { method: "POST", body: { appbar_max: +event.target.value } }));
    });
  }

  function registerSettingsSearch() {
    const K = window.MemuSettingsKit;
    if (!K?.registerSearch) return;
    const row = (sel) => `#pane-panels .sx-row:has(${sel})`;
    K.registerSearch([
      { page: "panels", label_ru: "Добавить панель", label_en: "Add a panel", desc_ru: "Погода, курсы, новости, To-Do, производительность ПК, лимиты ИИ, своя панель с ИИ", desc_en: "Weather, rates, news, To-Do, PC performance, AI limits, your own AI panel", keywords: "panel панель widget виджет создать create gallery галерея", anchor: '#pane-panels [data-mp-act="gallery"]' },
      { page: "panels", label_ru: "Местоположение для погоды", label_en: "Location for weather", desc_ru: "Определить по IP или указать город", desc_en: "Detect by IP or set a city", keywords: "location город city ip погода weather геолокация", anchor: row('[data-mp-act="locate"]') },
      { page: "panels", label_ru: "Единицы", label_en: "Units", desc_ru: "°C, м/с или °F, mph", desc_en: "°C, m/s or °F, mph", keywords: "units единицы цельсий фаренгейт celsius fahrenheit", anchor: row('[data-mp-seg="units"]') },
      { page: "panels", label_ru: "Обновлять панели автоматически", label_en: "Refresh panels automatically", keywords: "refresh обновление автоматически auto", anchor: row("[data-set-enabled]") },
      { page: "panels", label_ru: "Модель для панелей с ИИ", label_en: "Model for AI panels", keywords: "model модель ai ии news новости", anchor: row("[data-set-model]") },
      { page: "panels", label_ru: "Мини-панелей в верхней панели", label_en: "Mini panels in the top bar", keywords: "mini мини chip чип top bar верхняя", anchor: row("[data-set-appbar]") },
    ]);
  }

  if (!EMBED) registerSkill();
  function boot() {
    if (EMBED) { bindEvents(); load(); return; }
    bindEvents();
    bindSettings();
    registerSettingsSearch();
    const start = () => {
      if (typeof api !== "function" || !$q("#chat-view-wrap")) { setTimeout(start, 200); return; }
      ensureMounts();
      load();
      document.fonts?.ready?.then(() => fitAppbar());
    };
    start();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();

  return {
    load, render, systemBlock, inlineHtml, summary, mountSettings, openGallery, openEditor, enabled,

    _cardHtml: cardHtml, _chipHtml: chipHtml, _state: state, _raw: raw, SKILL_ID,

    cardHtml, chipHtml, miniOf, linesOf, ico, iconFor: (panel) => KIND_ICON[panel.kind] || "dot", refreshNow, rerender, state, subscribe: (fn) => { subscribers.add(fn); return () => subscribers.delete(fn); },

    syncLive,

    _lay: { state, byId, placed, cardHtml, chipHtml, render, call, act, notify, t, en, esc, ico, placeName, placeIcon, PLACES,
      sizeNames: SIZE_NAMES, sizeHints: SIZE_HINTS, openEditor, openGallery, openMenu, closeMenu, closePop, refreshNow, remove,
      layoutGrid, queueLayout, flash, isNew, syncLive, load, motionOff, EMBED },

    dataOf: (panel) => ({ ...(panel.kind === "system" ? systemData(panel, panel.data || {}) : (panel.data || {})), ...localData(panel) }),

    setPaused: (on) => {
      state.paused = !!on;
      if (state.paused) { clearTimeout(state.timer); clearInterval(state.liveTimer); state.liveTimer = 0; clearInterval(state.timersTimer); state.timersTimer = 0; }
      else load();
    },
  };
})();
window.MemuPanels = MemuPanels;
