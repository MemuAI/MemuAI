/* Catalog of bots for the «Characters» module.
   Inlined into main.js by MemuAI at the «@memu-part:catalog.js» marker
   (app/modules_host.py load_package), sharing main.js's scope.

   Transport: the module iframe cannot fetch the network itself, so search
   goes through the host bridge Memu.fetch() -> POST /api/pg/modules/search
   (action "fetch") -> app/websearch.py fetch_page(): a server-side GET
   with browser impersonation (curl_cffi), public HTTPS only, up to 8 MB
   for raw bodies. That is a hard limit: a catalog source can only join
   this catalog if its search works as an anonymous public GET with no
   cookies and no auth headers. No keys, no cookies, no Cloudflare bypass:
   if a catalog answers 403 from this computer, the UI says so honestly
   instead of pretending the search worked.

   Sources:
   - Chub (gateway.chub.ai/search): public search API, no key. Parameters
     documented by the character-archive scraper (deepwiki): search, first,
     page, sort (last_activity_at | created_at), asc, nsfw, nsfl,
     min_tokens, topics, excludetopics, namespace=characters,
     include_forks. Rows carry a real definition token count
     (tokenCount); tokenCountLabel ("1.2k") is only a fallback and is
     flagged as an estimate. One-click import works.
   - Pygmalion.chat (server.pygmalion.chat): Connect-RPC with GET, public,
     no key. The frontend's own transport is createConnectTransport with
     useHttpGet, so a search is a plain GET:
     .../galatea.v1.PublicCharacterService/CharacterSearch?connect=v1&
     encoding=json&message=<url-encoded JSON of {query, page, pageSize,
     orderBy, orderDescending, tagsNamesInclude, includeSensitive}>.
     The shape comes from the site's own protobuf descriptor
     (galatea/v1/character.proto, package galatea.v1). Rows carry
     personalityTokenCount (0 means "not computed", treated as unknown).
     One-click import works through the existing Pygmalion resolver
     (server.pygmalion.chat/api/export/character/{id}/v2).
   - SpicyChat (Typesense): the site publishes its search config at
     prod.nd-api.com/v2/applications/spicychat (public, no key): the
     collection name (public_characters_alias) and a scoped search-only
     key, fetched at runtime — nothing is hardcoded. Search is a GET on
     ts-lb.nd-api.com/collections/<collection>/documents/search with the
     key as the x-typesense-api-key query parameter. Rows carry a real
     token_count. Import in one click does NOT work: the full character
     definition needs a signed-in session (GET /v2/characters/{id}
     answers 401 anonymously), so SpicyChat rows offer "Open on the
     site" instead of "To chat", with an explanation. SpicyChat import,
     character.ai and JanitorAI all need the same missing piece: a user
     login in the app + an authenticated host fetch (designed, not built,
     see the fixlog).
   - character.ai and JanitorAI are NOT sources here: both need a user
     session (character.ai: "Authorization: Token <token>" + the
     web-next-auth cookie; JanitorAI: a login token), and the app has no
     login flow or authenticated fetch yet. They were deliberately left
     OUT of the selector instead of being shown as dead entries — a
     selector must only list catalogs that actually work.

   "Hide Low Quality Bots" hides rows whose definition token count is
   below CATALOG_LOW_TOKENS (500). The threshold is sent to the API as
   min_tokens where the API supports it (Chub) and re-checked on the
   client for every source; rows with no token data at all are kept
   (never hidden on unknown data) and marked as such. */
var CATALOG_LOW_TOKENS = 500;
var CATALOG_PAGE_SIZE = 24;

var CATALOG_SOURCES = {
  chub: {
    id: "chub", label: "Chub", hasTokens: true, searchable: true,
    searchBase: "https://gateway.chub.ai/search"
  },
  pygmalion: {
    id: "pygmalion", label: "Pygmalion.chat", hasTokens: true, searchable: true,
    searchBase: "https://server.pygmalion.chat/galatea.v1.PublicCharacterService/CharacterSearch"
  },
  spicychat: {
    id: "spicychat", label: "SpicyChat", hasTokens: true, searchable: true,
    /* The search host/collection/key come from the site's own public
       config (prod.nd-api.com/v2/applications/spicychat), fetched at
       runtime by catalogSpicyConfig(); nothing is hardcoded here. */
    configUrl: "https://prod.nd-api.com/v2/applications/spicychat",
    searchBase: "https://ts-lb.nd-api.com/collections"
  }
};

/* In-memory state of the catalog view (not persisted; the preferences
   below live in S.settings.catalog and are saved with the settings). */
var CAT = {
  rows: [], total: 0, page: 1, hasMore: false, loading: false,
  error: "", query: "", tag: "", tags: [], include: "", exclude: "", request: 0, loaded: false,
  spicyConfig: null, spicyConfigPromise: null
};

function catalogSettings() {
  S.settings.catalog = S.settings.catalog || {};
  var c = S.settings.catalog;
  if (typeof c.hideLow !== "boolean") c.hideLow = false;
  if (typeof c.nsfw !== "boolean") c.nsfw = false;
  if (typeof c.sort !== "string") c.sort = "default";
  if (typeof c.source !== "string") c.source = "chub";
  /* Older versions listed JanitorAI in the selector; it was removed
     because it has no catalog. A saved "janitor" value falls back. */
  if (!CATALOG_SOURCES[c.source]) c.source = "chub";
  return c;
}

/* ---------------- pure helpers (tested in Node, see
   tests/round29_characters_catalog_test.py) ---------------- */

/* "1.2k" -> 1200, "3.4M" -> 3400000, "850" -> 850, "1,234" -> 1234. */
function catalogParseTokenLabel(label) {
  if (label === undefined || label === null) return null;
  var s = String(label).trim().replace(/,/g, "").replace(/\s+/g, "");
  if (!s) return null;
  var m = /^([0-9]+(?:\.[0-9]+)?)([kKmM]?)$/.exec(s);
  if (!m) return null;
  var n = parseFloat(m[1]);
  if (m[2] === "k" || m[2] === "K") n *= 1000;
  else if (m[2] === "M") n *= 1000000;
  if (!isFinite(n) || n < 0) return null;
  return Math.round(n);
}

function catalogNum(value) {
  /* Number(null) is 0 and Number("") is 0: an explicitly null count from
     the API (Chub search returns tokenCount: null) must stay "unknown",
     not become a confident zero — a zero would make Hide Low Quality hide
     every row. */
  if (value === null || value === undefined || value === "") return null;
  var n = Number(value);
  return isFinite(n) ? n : null;
}

/* Token count of one Chub search node: the API's own tokenCount when
   present, the formatted tokenCountLabel only as an estimate. */
function catalogChubTokens(node) {
  var v = catalogNum(node.tokenCount);
  if (v !== null) return { value: Math.round(v), estimated: false };
  v = catalogNum(node.token_count);
  if (v !== null) return { value: Math.round(v), estimated: false };
  v = catalogNum(node.tokens);
  if (v !== null) return { value: Math.round(v), estimated: false };
  /* Live search rows carry the count in nTokens (tokenCount is null there);
     without this, every Chub row reported "token count unknown" and Hide
     Low Quality Bots never hid anything. */
  v = catalogNum(node.nTokens);
  if (v !== null) return { value: Math.round(v), estimated: false };
  var fromLabel = catalogParseTokenLabel(node.tokenCountLabel || node.token_count_label || "");
  if (fromLabel !== null) return { value: fromLabel, estimated: true };
  return { value: null, estimated: false };
}

function catalogStrList(value) {
  if (!Array.isArray(value)) return [];
  var out = [];
  value.forEach(function (x) {
    var s = String(x || "").trim();
    if (s && out.indexOf(s) < 0) out.push(s);
  });
  return out;
}

/* One search node -> one catalog row. Returns null for junk. */
function catalogNormalizeChub(node) {
  if (!node || typeof node !== "object") return null;
  /* Search nodes arrive bare, but the {node: {...}} wrapper exists in the
     wild (SillyTavern-style clients unwrap it); without this, a wrapped
     answer made every row vanish silently, avatars included. */
  if (node.node && typeof node.node === "object") node = node.node;
  var fullPath = String(node.fullPath || node.full_path || "").replace(/^\/+/, "");
  var creator = "";
  if (node.creator && typeof node.creator === "object") creator = String(node.creator.username || node.creator.name || "");
  creator = creator || String(node.username || node.creator_name || node.creator || "");
  if (!creator && fullPath.indexOf("/") > 0) creator = fullPath.split("/")[0];
  var name = String(node.name || node.title || "").trim();
  if (!name && !fullPath) return null;
  var tokens = catalogChubTokens(node);
  var chats = catalogNum(node.nChats);
  if (chats === null) chats = catalogNum(node.chat_count);
  if (chats === null) chats = catalogNum(node.n_chats);
  var stars = catalogNum(node.starCount);
  if (stars === null) stars = catalogNum(node.stars);
  var rating = catalogNum(node.rating);
  return {
    source: "chub",
    id: fullPath || String(node.id || name),
    name: name || fullPath,
    description: String(node.tagline || node.description || "").trim(),
    creator: creator,
    avatarUrl: String(node.max_res_url || (fullPath ? "https://avatars.charhub.io/avatars/" + fullPath.split("/").map(encodeURIComponent).join("/") + "/chara_card_v2.png" : "") || node.avatar_url || node.avatarUrl || node.image_url || ""),
    avatarFallback: String(node.avatar_url || node.avatarUrl || node.image_url || ""),
    pageUrl: fullPath ? "https://chub.ai/characters/" + fullPath : "",
    tags: catalogStrList(node.topics || node.tags).slice(0, 12),
    tokens: tokens.value,
    tokensEstimated: tokens.estimated,
    chats: chats,
    stars: stars,
    rating: rating,
    createdAt: node.createdAt || node.created_at || "",
    lastActivityAt: node.lastActivityAt || node.last_activity_at || "",
    nsfw: !!node.nsfw_image,
    importable: true
  };
}

/* The search endpoint changed its envelope before (data.nodes vs a bare
   array); accept every shape seen in the wild and never throw on junk. */
function catalogParseChub(json) {
  if (!json || typeof json !== "object") return { rows: [], total: 0 };
  var data = (json.data && typeof json.data === "object") ? json.data : json;
  var nodes = data.nodes || data.results || data.characters || (Array.isArray(data) ? data : []);
  if (!Array.isArray(nodes)) nodes = [];
  var rows = [];
  nodes.forEach(function (node) {
    var row = catalogNormalizeChub(node);
    if (row) rows.push(row);
  });
  var total = catalogNum(data.count);
  if (total === null) total = catalogNum(json.count);
  if (total === null) total = rows.length;
  return { rows: rows, total: total };
}

function catalogChubSearchUrl(opts) {
  opts = opts || {};
  var q = [];
  function add(key, value) {
    if (value === undefined || value === null || value === "") return;
    q.push(encodeURIComponent(key) + "=" + encodeURIComponent(String(value)));
  }
  add("search", opts.query || "");
  add("first", CATALOG_PAGE_SIZE);
  add("page", Math.max(1, opts.page || 1));
  add("namespace", "characters");
  add("nsfw", opts.nsfw ? "true" : "false");
  add("nsfl", opts.nsfw ? "true" : "false");
  if (opts.sort === "new") { add("sort", "created_at"); add("asc", "false"); }
  else if (opts.sort === "active") { add("sort", "last_activity_at"); add("asc", "false"); }
  if (opts.hideLow) add("min_tokens", CATALOG_LOW_TOKENS);
  if (opts.tag) add("topics", opts.tag);
  return CATALOG_SOURCES.chub.searchBase + "?" + q.join("&");
}

/* ---------------- Pygmalion.chat (Connect-RPC over GET) ---------------- */

/* Sort control -> Pygmalion's CharacterSearchOrderBy enum (from the
   site's own protobuf descriptor, galatea/v1/character.proto).
   "chats" has no API equivalent, so the search falls back to downloads
   and the client re-sorts by chatCount afterwards. */
var CATALOG_PYGMALION_ORDER = {
  "default": "downloads", active: "updated_at", "new": "created_at",
  tokens: "token_count", chats: "downloads"
};

function catalogPygmalionSearchUrl(opts) {
  opts = opts || {};
  var msg = {
    query: opts.query || "",
    page: Math.max(1, opts.page || 1),
    pageSize: CATALOG_PAGE_SIZE,
    orderBy: CATALOG_PYGMALION_ORDER[opts.sort] || "downloads",
    orderDescending: true,
    includeSensitive: !!opts.nsfw
  };
  if (opts.tag) msg.tagsNamesInclude = [opts.tag];
  return CATALOG_SOURCES.pygmalion.searchBase +
    "?connect=v1&encoding=json&message=" + encodeURIComponent(JSON.stringify(msg));
}

/* One CharacterMinimal -> one catalog row. personalityTokenCount is 0 for
   characters where the site never computed it: 0 means "unknown", never
   "empty", so it becomes null and the row is never hidden by Hide Low. */
function catalogNormalizePygmalion(node) {
  if (!node || typeof node !== "object") return null;
  var id = String(node.id || "").trim();
  var name = String(node.displayName || node.name || "").trim();
  if (!name && !id) return null;
  var creator = "";
  if (node.owner && typeof node.owner === "object") {
    creator = String(node.owner.displayName || node.owner.username || "");
  }
  creator = creator || String(node.creator || "");
  var tokens = catalogNum(node.personalityTokenCount);
  if (tokens !== null && tokens <= 0) tokens = null;
  var chats = catalogNum(node.chatCount);
  if (chats === null) chats = catalogNum(node.chat_count);
  return {
    source: "pygmalion",
    id: id || name,
    name: name || id,
    description: String(node.description || "").trim(),
    creator: creator,
    avatarUrl: String(node.avatarUrl || node.avatar_url || ""),
    pageUrl: id ? "https://pygmalion.chat/character/" + id : "",
    tags: catalogStrList(node.tags).slice(0, 12),
    tokens: tokens,
    tokensEstimated: false,
    chats: chats,
    stars: catalogNum(node.stars),
    rating: null,
    createdAt: node.createdAt || node.created_at || "",
    lastActivityAt: node.updatedAt || node.updated_at || "",
    importable: true
  };
}

/* Connect JSON: {totalItems: "55" (int64 arrives as a string),
   characters: [CharacterMinimal]}. */
function catalogParsePygmalion(json) {
  if (!json || typeof json !== "object") return { rows: [], total: 0 };
  var nodes = json.characters || json.nodes || json.results || [];
  if (!Array.isArray(nodes)) nodes = [];
  var rows = [];
  nodes.forEach(function (node) {
    var row = catalogNormalizePygmalion(node);
    if (row) rows.push(row);
  });
  var total = catalogNum(json.totalItems);
  if (total === null) total = catalogNum(json.total);
  if (total === null) total = rows.length;
  return { rows: rows, total: total };
}

/* ---------------- SpicyChat (Typesense search over GET) ---------------- */

/* Sort control -> Typesense sort_by. token_count is NOT in the index
   schema for filtering or sorting (verified live: the server answers
   "Could not find a field named `token_count`"), so "tokens" fetches by
   popularity and the client re-sorts the page by token count — the same
   arrangement Chub uses for its tokens/chats sorts. */
var CATALOG_SPICY_SORT = {
  "default": "num_messages:desc", active: "num_messages_24h:desc",
  "new": "createdAt:desc", tokens: "num_messages:desc", chats: "num_messages:desc"
};

var CATALOG_SPICY_QUERY_BY = "name,title,tags,creator_username,character_id,type";
var CATALOG_SPICY_AVATAR_BASE = "https://cdn.nd-api.com/";

/* The site's own public application config: the search collection name
   and a scoped search-only key. Fetched once per catalog session and
   cached in memory only — the key is what the website itself ships to
   every visitor's browser, so there is nothing secret to protect, but
   it is still never written to disk. */
function catalogSpicyConfig() {
  if (CAT.spicyConfig) return Promise.resolve(CAT.spicyConfig);
  if (CAT.spicyConfigPromise) return CAT.spicyConfigPromise;
  CAT.spicyConfigPromise = Memu.fetch({
    url: CATALOG_SOURCES.spicychat.configUrl, raw: true, limit: 400000
  }).then(function (res) {
    var json;
    try { json = JSON.parse(res.text || ""); }
    catch (e) { throw new Error(t("catalog_bad_answer")); }
    var tc = (json && json.typesenseConfig) || {};
    var collection = String(tc.collectionNamePublicCharacter || "").trim();
    var apiKey = String(tc.apiKeyPublicCharacter || "").trim();
    if (!collection || !apiKey) throw new Error(t("catalog_bad_answer"));
    CAT.spicyConfig = { collection: collection, apiKey: apiKey };
    return CAT.spicyConfig;
  }).catch(function (e) {
    CAT.spicyConfigPromise = null;
    throw e;
  });
  return CAT.spicyConfigPromise;
}

function catalogSpicySearchUrl(cfg, opts) {
  opts = opts || {};
  var q = [];
  function add(key, value) {
    if (value === undefined || value === null || value === "") return;
    q.push(encodeURIComponent(key) + "=" + encodeURIComponent(String(value)));
  }
  add("q", opts.query || "*");
  add("query_by", CATALOG_SPICY_QUERY_BY);
  add("per_page", CATALOG_PAGE_SIZE);
  add("page", Math.max(1, opts.page || 1));
  add("sort_by", CATALOG_SPICY_SORT[opts.sort] || "num_messages:desc");
  var filters = [];
  if (!opts.nsfw) filters.push("is_nsfw:false");
  if (opts.tag) filters.push("tags:=" + opts.tag);
  if (filters.length) add("filter_by", filters.join(" && "));
  add("x-typesense-api-key", cfg.apiKey);
  return CATALOG_SOURCES.spicychat.searchBase + "/" +
    encodeURIComponent(cfg.collection) + "/documents/search?" + q.join("&");
}

/* One Typesense document -> one catalog row. avatar_url is a relative
   path on the site's CDN. importable is false: the full definition
   needs a signed-in session (see the header comment), so the row offers
   "Open on the site" instead of "To chat". */
function catalogNormalizeSpicy(doc) {
  if (!doc || typeof doc !== "object") return null;
  var id = String(doc.character_id || doc.id || "").trim();
  var name = String(doc.name || doc.title || "").trim();
  if (!name && !id) return null;
  var avatar = String(doc.avatar_url || "");
  if (avatar && avatar.indexOf("http://") !== 0 && avatar.indexOf("https://") !== 0) {
    avatar = CATALOG_SPICY_AVATAR_BASE + avatar.replace(/^\/+/, "");
  }
  var tokens = catalogNum(doc.token_count);
  if (tokens !== null && tokens <= 0) tokens = null;
  return {
    source: "spicychat",
    id: id || name,
    name: name || id,
    description: String(doc.title || ""),
    greeting: String(doc.greeting || ""),
    creator: String(doc.creator_username || ""),
    avatarUrl: avatar,
    pageUrl: id ? "https://spicychat.ai/chatbot/" + id : "",
    tags: catalogStrList(doc.tags).slice(0, 12),
    tokens: tokens,
    tokensEstimated: false,
    chats: catalogNum(doc.num_messages),
    stars: null,
    rating: null,
    createdAt: doc.createdAt || doc.created_at || "",
    lastActivityAt: doc.updatedAt || doc.updated_at || "",
    nsfw: !!doc.is_nsfw,
    importable: false
  };
}

/* Typesense GET: {found: N, hits: [{document: {...}}], page, out_of}. */
function catalogParseSpicy(json) {
  if (!json || typeof json !== "object") return { rows: [], total: 0 };
  var hits = json.hits || json.results || [];
  if (!Array.isArray(hits)) hits = [];
  var rows = [];
  hits.forEach(function (hit) {
    var row = catalogNormalizeSpicy(hit && hit.document ? hit.document : hit);
    if (row) rows.push(row);
  });
  var total = catalogNum(json.found);
  if (total === null) total = catalogNum(json.total);
  if (total === null) total = rows.length;
  return { rows: rows, total: total };
}

/* Client-side pass: hide-low-quality (unknown token counts are kept),
   tag filter (when the API did not already apply it), sorting that the
   API cannot do (by tokens / by chats / by name). */
function catalogFilterRows(rows, opts) {
  opts = opts || {};
  var out = (rows || []).filter(function (r) {
    if (!r) return false;
    if (opts.hideLow && r.tokens !== null && r.tokens !== undefined && r.tokens < CATALOG_LOW_TOKENS) return false;
    if (opts.tag && (r.tags || []).indexOf(opts.tag) < 0) return false;
    var tags = (r.tags || []).map(function (tag) { return tag.toLowerCase(); });
    if (opts.include && !opts.include.every(function (tag) { return tags.includes(tag); })) return false;
    if (opts.exclude && opts.exclude.some(function (tag) { return tags.includes(tag); })) return false;
    return true;
  });
  if (opts.sort === "tokens") out.sort(function (a, b) { return (b.tokens || 0) - (a.tokens || 0); });
  else if (opts.sort === "chats") out.sort(function (a, b) { return (b.chats || 0) - (a.chats || 0); });
  else if (opts.sort === "name") out.sort(function (a, b) { return String(a.name).localeCompare(String(b.name)); });
  return out;
}

function catalogTopTags(rows, limit) {
  var counts = Object.create(null);
  (rows || []).forEach(function (r) {
    (r.tags || []).forEach(function (tag) { counts[tag] = (counts[tag] || 0) + 1; });
  });
  return Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a]; }).slice(0, limit || 12);
}

/* ---------------- view ---------------- */

function openCatalog() {
  if (S.view !== "catalog") catalogSidebarOpen = false;
  S.view = "catalog";
  render();
  if (!CAT.loaded) catalogRun(true);
}

/* One search, whatever the source: resolve to {url, parse} and fetch it.
   SpicyChat first fetches its public search config (cached per session);
   Chub and Pygmalion build the URL synchronously. */
function catalogSearchRequest(cfg, page) {
  var opts = { query: CAT.query, page: page, sort: cfg.sort, nsfw: cfg.nsfw, hideLow: cfg.hideLow, tag: CAT.tag };
  if (cfg.source === "pygmalion") {
    return Promise.resolve({ url: catalogPygmalionSearchUrl(opts), parse: catalogParsePygmalion });
  }
  if (cfg.source === "spicychat") {
    return catalogSpicyConfig().then(function (spicyCfg) {
      return { url: catalogSpicySearchUrl(spicyCfg, opts), parse: catalogParseSpicy };
    });
  }
  return Promise.resolve({ url: catalogChubSearchUrl(opts), parse: catalogParseChub });
}

function catalogRun(reset) {
  if (CAT.loading && !reset) return;
  var cfg = Object.assign({}, catalogSettings());
  var request = ++CAT.request;
  var page = reset ? 1 : CAT.page + 1;
  if (reset) { CAT.rows = []; CAT.total = 0; CAT.hasMore = false; CAT.error = ""; CAT.tags = []; $("#main").scrollTop = 0; }
  var terms = function (value) { return value.split(",").map(function (tag) { return tag.trim().toLowerCase(); }).filter(Boolean); };
  var filters = { hideLow: cfg.hideLow, tag: CAT.tag, sort: cfg.sort, include: terms(CAT.include), exclude: terms(CAT.exclude) };
  var src = CATALOG_SOURCES[cfg.source] || CATALOG_SOURCES.chub;
  if (!src.searchable) {
    CAT.error = t(src.unavailable || "catalog_unavailable");
    CAT.loaded = true;
    renderCatalog();
    return;
  }
  CAT.loading = true;
  renderCatalog();
  catalogSearchRequest(cfg, page).then(function (req) {
    return Memu.fetch({ url: req.url, raw: true, limit: 400000 }).then(function (res) {
      var json;
      try { json = JSON.parse(res.text || ""); }
      catch (e) { throw new Error(t("catalog_bad_answer")); }
      var parsed = req.parse(json);
      if (request !== CAT.request) return;
      var known = new Set(CAT.rows.map(function (row) { return row.id; }));
      var filtered = catalogFilterRows(parsed.rows, filters).filter(function (row) {
        if (known.has(row.id)) return false;
        known.add(row.id); return true;
      });
      CAT.rows = catalogFilterRows(CAT.rows.concat(filtered), filters);
      CAT.page = page;
      CAT.total = parsed.total;
      CAT.hasMore = parsed.rows.length >= CATALOG_PAGE_SIZE && (!parsed.total || page * CATALOG_PAGE_SIZE < parsed.total);
      CAT.tags = Array.from(new Set(CAT.tags.concat(catalogTopTags(parsed.rows, 18)))).slice(0, 30);
      CAT.error = "";
      CAT.loaded = true;
    });
  }).catch(function (e) {
    if (request === CAT.request) CAT.error = catalogFetchError(e, cfg.source);
  }).then(function () {
    if (request !== CAT.request) return;
    CAT.loading = false;
    renderCatalog();
  });
}

/* Turn a transport failure into a plain-language explanation: a 403
   from Chub is a geo-block of this computer, not a broken catalog. */
function catalogFetchError(e, source) {
  var msg = String((e && e.message) || e || "");
  if (/HTTP 403/.test(msg)) return t(source === "chub" ? "catalog_err_403" : "catalog_err_403_other");
  if (/локальную или частную сеть|private network/i.test(msg)) return t("catalog_err_private_dns");
  if (/HTTP 404/.test(msg)) return t(source === "chub" ? "catalog_err_404" : "catalog_err_http", { message: msg });
  if (/HTTP 4\d\d/.test(msg) || /HTTP 5\d\d/.test(msg)) return t("catalog_err_http", { message: msg });
  if (/MemuAI не ответил|timed out|timeout/i.test(msg)) return t("catalog_err_timeout");
  return t("catalog_err_other", { message: msg });
}

function renderCatalog() {
  var main = $("#main");
  if (!main || S.view !== "catalog") return;
  var scrollTop = main.scrollTop;
  syncNarrowSidebar(false);
  main.innerHTML = "";
  main.className = "ch-main catalog";
  var cfg = catalogSettings();
  var searchInput, sortSel;

  /* Header: back home, title, total, import. */
  var head = h("div", { class: "ch-cat-head" }, [
    iconBtn("home", "home", goHome),
    iconBtn("sidebar", "toggle_sidebar", toggleSidebar),
    h("div", { class: "ch-cat-titlewrap" }, [
      h("div", { class: "ch-cat-title", text: t("catalog_title") }),
      CAT.loaded && !CAT.error ? h("div", { class: "ch-cat-count", text: t("catalog_found", { count: fmtNum(CAT.rows.length), total: fmtNum(CAT.total) }) }) : null
    ]),
    h("span", { class: "mu-grow" }),
    h("button", { class: "mu-btn", type: "button", html: icon("upload", 15) + esc(t("import")), onclick: function () { importFlow(); } })
  ]);
  main.appendChild(head);

  /* Toolbar: source pills, search, sort, toggles, tags — one card. */
  var toolbar = h("div", { class: "ch-cat-toolbar" });
  var srcRow = h("div", { class: "ch-cat-srcrow", role: "group", "aria-label": t("catalog_source") });
  Object.keys(CATALOG_SOURCES).forEach(function (key) {
    var src = CATALOG_SOURCES[key];
    var pill = h("button", {
      class: "ch-cat-src" + (key === cfg.source ? " on" : ""),
      type: "button", "aria-pressed": key === cfg.source ? "true" : "false", text: src.label,
      onclick: function () {
        if (cfg.source === key) return;
        cfg.source = key; CAT.tag = ""; saveSettings();
        CAT.query = searchInput.value.trim();
        catalogRun(true);
      }
    });
    srcRow.appendChild(pill);
  });
  toolbar.appendChild(srcRow);

  var searchRow = h("div", { class: "ch-cat-searchrow" });
  searchInput = h("input", { class: "mu-input ch-cat-search", type: "search", placeholder: t("catalog_search_ph"), "aria-label": t("catalog_search_ph") });
  searchInput.value = CAT.query;
  searchInput.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); CAT.query = searchInput.value.trim(); catalogRun(true); } });
  var searchBtn = h("button", { class: "mu-btn primary", type: "button", html: icon("search", 15) + esc(t("search")), onclick: function () { CAT.query = searchInput.value.trim(); catalogRun(true); } });
  searchRow.appendChild(h("span", { class: "ch-cat-search-ic", html: icon("search", 16) }));
  searchRow.appendChild(searchInput);
  searchRow.appendChild(searchBtn);
  toolbar.appendChild(searchRow);

  var controls = h("div", { class: "ch-cat-controls" });
  sortSel = h("select", { class: "mu-select ch-cat-sort", "aria-label": t("catalog_sort") });
  [["default", t("catalog_sort_default")], ["active", t("catalog_sort_active")], ["new", t("catalog_sort_new")],
   ["tokens", t("catalog_sort_tokens")], ["chats", t("catalog_sort_chats")]].forEach(function (o) {
    var opt = h("option", { value: o[0], text: o[1] });
    if (o[0] === cfg.sort) opt.selected = true;
    sortSel.appendChild(opt);
  });
  sortSel.addEventListener("change", function () { cfg.sort = sortSel.value; saveSettings(); catalogRun(true); });
  controls.appendChild(h("span", { class: "ch-cat-sort-ic", html: icon("sort", 15) }));
  controls.appendChild(sortSel);
  var nsfwBox = h("button", { class: "ch-cat-toggle" + (cfg.nsfw ? " on" : ""), type: "button", "aria-pressed": cfg.nsfw ? "true" : "false",
    html: icon("eye", 14) + esc(t("catalog_nsfw")), onclick: function () {
      cfg.nsfw = !cfg.nsfw; saveSettings(); catalogRun(true);
    } });
  controls.appendChild(nsfwBox);
  var hideBox = h("button", { class: "ch-cat-toggle" + (cfg.hideLow ? " on" : ""), type: "button", title: t("catalog_hide_low_hint"),
    "aria-pressed": cfg.hideLow ? "true" : "false", html: icon("shield", 14) + esc(t("catalog_hide_low")), onclick: function () {
      cfg.hideLow = !cfg.hideLow; saveSettings(); catalogRun(true);
    } });
  controls.appendChild(hideBox);
  toolbar.appendChild(controls);
  if (cfg.hideLow) {
    toolbar.appendChild(h("p", { class: "mu-muted ch-cat-hint", text: t("catalog_hide_low_hint") }));
  }
  if (cfg.source === "spicychat") {
    toolbar.appendChild(h("p", { class: "mu-muted ch-cat-hint", text: t("catalog_spicy_hint") }));
  }

  if (CAT.tags.length) {
    var tagsRow = h("div", { class: "ch-tagbar ch-cat-tags" });
    tagsRow.appendChild(h("button", { class: "ch-chip" + (!CAT.tag ? " on" : ""), type: "button", text: t("all"), onclick: function () { CAT.tag = ""; catalogRun(true); } }));
    CAT.tags.forEach(function (tag) {
      tagsRow.appendChild(h("button", { class: "ch-chip" + (CAT.tag === tag ? " on" : ""), type: "button", text: tag, onclick: function () {
        CAT.tag = CAT.tag === tag ? "" : tag;
        catalogRun(true);
      } }));
    });
    toolbar.appendChild(tagsRow);
  }
  var tags = h("details", { class: "ch-cat-filter" }, [
    h("summary", { text: t("catalog_tag_filters") }),
    h("div", { class: "ch-cat-filter-form" }, [
      h("label", {}, [h("span", { text: t("catalog_include_tags") }), h("input", { class: "mu-input", name: "include", value: CAT.include, maxlength: "300" })]),
      h("label", {}, [h("span", { text: t("catalog_exclude_tags") }), h("input", { class: "mu-input", name: "exclude", value: CAT.exclude, maxlength: "300" })]),
      h("button", { class: "mu-btn", type: "button", text: t("catalog_apply_tags") })
    ])
  ]);
  tags.open = !!(CAT.include || CAT.exclude);
  function applyTags() {
    CAT.include = tags.querySelector("[name=include]").value.trim();
    CAT.exclude = tags.querySelector("[name=exclude]").value.trim();
    CAT.query = searchInput.value.trim();
    catalogRun(true);
  }
  tags.querySelector("button").addEventListener("click", applyTags);
  tags.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" || !e.target.matches("input")) return;
    e.preventDefault(); applyTags();
  });
  toolbar.appendChild(tags);
  main.appendChild(toolbar);

  /* Status: skeleton while loading, a readable error block, or nothing. */
  if (CAT.loading && !CAT.rows.length) {
    var skel = h("div", { class: "ch-gallery ch-cat-grid", id: "catalog-grid" });
    for (var si = 0; si < 6; si++) {
      skel.appendChild(h("div", { class: "ch-cat-card ch-cat-skel", "aria-hidden": "true" }, [
        h("div", { class: "ch-cat-skel-img" }),
        h("div", { class: "ch-cat-skel-body" }, [
          h("div", { class: "ch-cat-skel-line w70" }),
          h("div", { class: "ch-cat-skel-line w40" }),
          h("div", { class: "ch-cat-skel-line w90" })
        ])
      ]));
    }
    main.appendChild(skel);
    main.appendChild(h("div", { class: "ch-cat-status" }, [
      h("span", { class: "mu-muted", text: t("catalog_loading") })
    ]));
    return;
  }
  if (CAT.error) {
    main.appendChild(h("div", { class: "ch-cat-errorbox" }, [
      h("div", { class: "ch-cat-error-ic", html: icon("warn", 28) }),
      h("div", { class: "ch-cat-error-title", text: t("catalog_error_title") }),
      h("div", { class: "ch-cat-error", text: CAT.error }),
      h("button", { class: "mu-btn primary", type: "button", text: t("retry"), onclick: function () { catalogRun(!CAT.rows.length); } })
    ]));
    if (CAT.rows.length) {
      var grid = h("div", { class: "ch-gallery ch-cat-grid", id: "catalog-grid" });
      CAT.rows.forEach(function (row) { grid.appendChild(catalogCard(row)); });
      main.appendChild(grid);
      main.scrollTop = scrollTop;
    }
    return;
  }

  if (!CAT.rows.length && CAT.loaded) {
    main.appendChild(h("div", { class: "ch-cat-empty" }, [
      h("div", { class: "ch-empty-ic", html: icon("search", 40) }),
      h("div", { class: "ch-empty-title", text: t("catalog_no_results") }),
      h("div", { class: "mu-muted", text: t("catalog_no_results_hint") }),
      h("button", { class: "mu-btn", type: "button", text: t("catalog_clear_filters"), onclick: function () {
        CAT.query = ""; CAT.tag = ""; CAT.include = ""; CAT.exclude = ""; cfg.hideLow = false; saveSettings(); catalogRun(true);
      } })
    ]));
  }

  var grid2 = h("div", { class: "ch-gallery ch-cat-grid", id: "catalog-grid" });
  CAT.rows.forEach(function (row) { grid2.appendChild(catalogCard(row)); });
  main.appendChild(grid2);

  var foot = h("div", { class: "ch-cat-foot" });
  if (CAT.loaded) foot.appendChild(h("span", { class: "mu-muted ch-cat-status", text: t("catalog_found", { count: fmtNum(CAT.rows.length), total: fmtNum(CAT.total) }) }));
  if (CAT.hasMore) {
    foot.appendChild(h("button", { class: "mu-btn", type: "button", text: CAT.loading ? t("catalog_loading") : t("catalog_load_more"),
      onclick: function () { if (!CAT.loading) catalogRun(false); } }));
  }
  main.appendChild(foot);
  main.scrollTop = scrollTop;
}

function catalogTokensText(row) {
  if (row.tokens === null || row.tokens === undefined) return t("catalog_tokens_unknown");
  return (row.tokensEstimated ? "≈" : "") + fmtNum(row.tokens) + " " + t("catalog_tokens_short");
}

/* One row of the grid: a large 2:3 avatar on top, the name / author /
   description / stats on a clean panel below — no text over the picture,
   so long names and descriptions stay readable. The whole card opens the
   detail view; the action button imports (or opens the site) without
   opening the detail view. */
function catalogCard(row) {
  var img = h("img", { src: row.avatarUrl || defaultAvatar(row.name, row.id), alt: "", loading: "lazy" });
  img.addEventListener("error", function onImgError() {
    if (row.avatarFallback && !img.dataset.fallback && row.avatarFallback !== row.avatarUrl) {
      img.dataset.fallback = "1"; img.src = row.avatarFallback; return;
    }
    img.removeEventListener("error", onImgError);
    img.src = defaultAvatar(row.name, row.id);
  });
  var stats = [];
  stats.push(catalogTokensText(row));
  if (row.chats) stats.push(t("catalog_chats", { count: fmtNum(row.chats) }));
  if (row.stars) stats.push("★ " + fmtNum(row.stars));
  var card = h("div", { class: "ch-cat-card", tabindex: "0", role: "button" }, [
    h("div", { class: "ch-cat-img" }, [
      img,
      row.nsfw ? h("span", { class: "ch-cat-nsfw", text: "18+" }) : null
    ]),
    h("div", { class: "ch-cat-body" }, [
      h("div", { class: "ch-cat-name", text: row.name }),
      row.creator ? h("div", { class: "ch-cat-creator", text: t("catalog_by", { name: row.creator }) }) : null,
      row.description ? h("div", { class: "ch-cat-desc", text: row.description }) : null,
      h("div", { class: "ch-cat-meta", text: stats.join(" · ") }),
      (row.tags || []).length ? h("div", { class: "ch-cat-tags" }, row.tags.slice(0, 3).map(function (tag) {
        return h("span", { class: "ch-chip small", text: tag });
      })) : null
    ]),
    h("div", { class: "ch-cat-foot" }, [catalogCardButton(row)])
  ]);
  card.addEventListener("click", function () { catalogOpenDetail(row); });
  card.addEventListener("keydown", function (e) {
    if (e.target !== card || (e.key !== "Enter" && e.key !== " ")) return;
    e.preventDefault(); catalogOpenDetail(row);
  });
  return card;
}

/* The card's action button: "To chat" (one-click import) where the
   source hands over a full card, "Open on the site" where it does not
   (SpicyChat: the definition needs a signed-in session). */
function catalogCardButton(row) {
  if (row.importable === false) {
    return h("button", { class: "mu-btn small ch-cat-imp", type: "button", html: icon("link", 14) + esc(t("catalog_open_site")), onclick: function (e) {
      e.stopPropagation();
      catalogOpenPage(row);
    } });
  }
  return h("button", { class: "mu-btn primary small ch-cat-imp", type: "button", html: icon("chat", 14) + esc(t("catalog_import_chat")), onclick: function (e) {
    e.stopPropagation();
    catalogImport(row, e.currentTarget);
  } });
}

/* Rows whose source cannot hand over a full card open the bot's page in
   the user's browser instead of pretending an import happened. */
function catalogOpenPage(row) {
  if (!row.pageUrl) { toast(t("catalog_no_page"), "error"); return; }
  if (typeof Memu !== "undefined" && Memu && typeof Memu.open === "function") {
    Memu.open({ url: row.pageUrl }).catch(function () { toast(row.pageUrl, "warn"); });
  } else {
    toast(row.pageUrl, "warn");
  }
}

function catalogOpenDetail(row) {
  var img = h("img", { class: "ch-cat-detail-av", src: row.avatarUrl || defaultAvatar(row.name, row.id), alt: "" });
  img.addEventListener("error", function onDetailImgError() {
    if (row.avatarFallback && !img.dataset.fallback && row.avatarFallback !== row.avatarUrl) {
      img.dataset.fallback = "1"; img.src = row.avatarFallback; return;
    }
    img.removeEventListener("error", onDetailImgError);
    img.src = defaultAvatar(row.name, row.id);
  });
  var stats = [catalogTokensText(row)];
  if (row.chats) stats.push(t("catalog_chats", { count: fmtNum(row.chats) }));
  if (row.stars) stats.push("★ " + fmtNum(row.stars));
  if (row.rating) stats.push(t("catalog_rating", { value: String(row.rating) }));
  var definition = h("div", { class: "ch-cat-definition", role: "status" });
  var body = h("div", { class: "ch-cat-detail" }, [
    h("div", { class: "ch-cat-detail-media" }, [
      img,
      row.nsfw ? h("span", { class: "ch-cat-nsfw", text: "18+" }) : null
    ]),
    h("div", { class: "ch-cat-detail-main" }, [
      h("div", { class: "ch-cat-detail-name", text: row.name }),
      row.creator ? h("div", { class: "mu-muted", text: t("catalog_by", { name: row.creator }) }) : null,
      h("div", { class: "ch-cat-stats" }, stats.map(function (s) { return h("span", { class: "ch-cat-stat", text: s }); })),
      row.description ? h("p", { text: row.description }) : h("p", { class: "mu-muted", text: t("catalog_no_description") }),
      row.greeting ? h("blockquote", { class: "ch-cat-greeting" }, [
        h("div", { class: "ch-cat-greeting-label", text: t("catalog_greeting") }),
        h("div", { text: row.greeting })
      ]) : null,
      definition,
      row.importable === false ? h("p", { class: "mu-muted ch-small", text: t("catalog_spicy_noimport") }) : null,
      (row.tags || []).length ? h("div", { class: "ch-cat-tags" }, row.tags.map(function (tag) {
        return h("span", { class: "ch-chip small", text: tag });
      })) : null,
      h("p", { class: "mu-muted ch-small", text: t("catalog_source_line", { source: CATALOG_SOURCES[row.source].label, page: row.pageUrl || "—" }) })
    ])
  ]);
  var buttons = [{ label: t("cancel"), kind: "" }];
  if (row.importable === false) {
    buttons.push({ label: t("catalog_open_site"), kind: "primary", onClick: function () { catalogOpenPage(row); } });
  } else {
    buttons.push({ label: t("catalog_import_chat"), kind: "primary", onClick: function () { catalogImport(row); } });
  }
  openModal({ title: row.name, icon: "users", wide: true, content: function (b) { b.appendChild(body); },
    buttons: buttons });
  if (row.importable === false || !row.pageUrl || !Memu.fetchImport) return;
  definition.textContent = t("catalog_loading_definition");
  Memu.fetchImport(row.pageUrl).then(function (res) {
    var file = res.file || {};
    return analyzeBytes(E.base64ToBytes(file.base64 || ""), file.name || res.name, file.mime,
      { source: res.source, page: res.page, kind: "character" });
  }).then(function (item) {
    if (!definition.isConnected) return;
    var data = item && item.kind === "character" && item.data;
    if (!data) throw new Error(t("catalog_not_character"));
    definition.replaceChildren();
    var fields = [
      [t("catalog_card_description"), data.description], [t("catalog_greeting"), data.first_mes],
      [t("personality"), data.personality], [t("scenario"), data.scenario],
      [t("catalog_creator_notes"), data.creator_notes], [t("catalog_examples"), data.mes_example]
    ];
    (Array.isArray(data.alternate_greetings) ? data.alternate_greetings : []).forEach(function (greeting, i) {
      fields.push([t("catalog_greeting") + " " + (i + 2), greeting]);
    });
    fields.forEach(function (field, i) {
      if (typeof field[1] !== "string" || !field[1].trim()) return;
      var section = h("details", {}, [h("summary", { text: field[0] }), h("p", { text: field[1] })]);
      section.open = i < 2;
      definition.appendChild(section);
    });
    if (!definition.childElementCount) definition.textContent = t("catalog_no_description");
  }).catch(function () {
    if (definition.isConnected) definition.textContent = t("catalog_definition_unavailable");
  });
}

/* Server avatar-error codes (app/character_import.py _avatar) -> a short
   human reason. Unknown codes are shown as-is; the toast never stays silent
   about a missing avatar. */
function catalogAvatarErrorText(code) {
  var c = String(code || "");
  if (c === "avatar_missing") return t("catalog_avatar_reason_missing");
  if (c === "avatar_http_403" || c === "avatar_blocked") return t("catalog_avatar_reason_blocked");
  if (c === "avatar_http_404" || c === "avatar_not_found") return t("catalog_avatar_reason_gone");
  if (c === "avatar_dns" || c === "avatar_timeout" || c === "avatar_network" || c === "avatar_private") return t("catalog_avatar_reason_network");
  if (c === "avatar_not_image" || c === "avatar_bad_data") return t("catalog_avatar_reason_bad");
  if (c === "avatar_too_large") return t("catalog_avatar_reason_big");
  if (c === "avatar_http" || c.indexOf("avatar_http_") === 0) return t("catalog_avatar_reason_http");
  return c || t("catalog_avatar_reason_missing");
}

/* One click: download the card the same way the import dialog does
   (Memu.fetchImport, with the host asking first when the source is not
   trusted yet), build the character with the full field mapping of
   createFromCard, and open the chat.
   The avatar has two chances: the bytes the server downloaded (res.avatar)
   and — when the server could not get them — the avatar URL the search
   result already carries (row.avatarUrl), stored as a link to the source
   site. A character is never created silently avatar-less: the person is
   told which of the three happened (downloaded / linked / missing). */
function catalogImport(row, btn) {
  if (!row.pageUrl) { toast(t("catalog_no_page"), "error"); return Promise.resolve(); }
  if (btn) { btn.disabled = true; btn.classList.add("loading"); }
  toast(t("catalog_importing", { name: row.name }));
  var avatarLinked = false;
  var avatarReason = "";
  return Memu.fetchImport(row.pageUrl).then(function (res) {
    var file = res.file || {};
    var bytes = E.base64ToBytes(file.base64 || "");
    avatarReason = String(res.avatar_error || "");
    return analyzeBytes(bytes, file.name || res.name, file.mime,
      { source: res.source, page: res.page, avatar: res.avatar, kind: "character" });
  }).then(function (item) {
    if (!item || item.kind !== "character") throw new Error(t("catalog_not_character"));
    var avatar = item.avatar || "";
    if (avatar) return createFromCard(item.json, avatar, { data: item.data, source: item.source, page: item.page });
    if (!row.avatarUrl) return createFromCard(item.json, "", { data: item.data, source: item.source, page: item.page });
    /* The server could not download the picture, but the search answer
       already gave us its address. First check the address is alive —
       a dead link stored as the avatar would show a broken picture —
       then keep the character WITH an avatar, linked to the source site,
       instead of a faceless placeholder. createFromCard tries to resize
       it; a cross-origin picture cannot be read back from the canvas,
       so the URL itself is stored. */
    return loadImage(row.avatarUrl).then(function () {
      avatarLinked = true;
      return createFromCard(item.json, row.avatarUrl, { data: item.data, source: item.source, page: item.page });
    }, function () {
      return createFromCard(item.json, "", { data: item.data, source: item.source, page: item.page });
    });
  }).then(function (char) {
    toast(t("catalog_imported", { name: char.data.name }), "ok");
    if (!char.avatar) {
      toast(t("catalog_avatar_missing", { reason: catalogAvatarErrorText(avatarReason) }), "warn");
    } else if (avatarLinked) {
      toast(t("catalog_avatar_linked"), "warn");
    }
    return openCharacter(char.id);
  }).catch(function (e) {
    toast(errText(e), "error");
  }).then(function () {
    if (btn) { btn.disabled = false; btn.classList.remove("loading"); }
  });
}
