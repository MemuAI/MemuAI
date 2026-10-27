(() => {
  const language = () => typeof Memu !== "undefined" ? Memu.lang : App.lang;
  const L = (ru, en) => language() === "en" ? en : ru;
  const button = document.createElement("button");
  button.type = "button"; button.dataset.go = "recommendations";
  button.innerHTML = `${icon("sparkle")}<span>${esc(L("Рекомендации", "Recommendations"))}</span>`;
  $("#nav").insertBefore(button, $("#nav [data-go=settings]"));
  const relabel = () => { button.querySelector("span").textContent = L("Рекомендации", "Recommendations"); };
  if (typeof Memu !== "undefined" && Memu.onLanguage) Memu.onLanguage(relabel);
  if (typeof App !== "undefined" && App.onLanguage) App.onLanguage(relabel);

  VIEWS.recommendations = async host => {
    host.innerHTML = `<section class="mm-section"><h1 class="mm-h1">${esc(L("Рекомендации", "Recommendations"))}</h1><p class="mm-sub">${esc(L("Похожие треки из YouTube Music на основе вашей медиатеки.", "Similar tracks from YouTube Music, based on your library."))}</p><button type="button" class="mm-btn tint" data-rec-run>${icon("refresh")}${esc(L("Обновить подборку", "Refresh recommendations"))}</button><p class="mm-sub" data-rec-status></p><div class="mm-online" data-rec-results></div></section>`;
    const list = $("[data-rec-results]", host), status = $("[data-rec-status]", host), run = $("[data-rec-run]", host);
    function draw(rows) {
      list.innerHTML = rows.map((r, i) => onlineRow(r, i).replace('<div class="mm-oacts">', `<div class="mm-oacts">${!onlLibTrack(r) ? `<button type="button" class="mm-btn ghost sm" data-play>${icon("play")}${esc(t("play"))}</button>` : ""}`)).join("");
      $$("img[data-fb]", list).forEach(img => { img.onerror = () => { img.onerror = null; img.src = img.dataset.fb; }; });
      list.onclick = e => { const row = e.target.closest("[data-k]"), item = row && rows[Number(row.dataset.k)]; if (!item) return; if (e.target.closest("[data-play]")) onlineListen(item, {}); else if (e.target.closest("[data-dl]")) onlineDownload(item, {}, () => { const b = $("[data-dl]", row); if (b) b.disabled = true; }); else if (e.target.closest("[data-more]")) onlineMenu(item, e.target.closest("[data-more]"), {}); else onlineSheet(item, {}); };
    }
    if (Array.isArray(S.prefs.recommendations)) draw(S.prefs.recommendations);
    run.onclick = async () => {
      if (!S.tracks.length) { status.textContent = L("Добавьте треки в медиатеку, чтобы получить рекомендации.", "Add tracks to your library to get recommendations."); return; }
      run.disabled = true;
      const seeds = S.tracks.slice(), matches = [], seen = new Set(), existing = new Set(seeds.map(x => `${x.artist || ""}|${x.title || ""}`.toLowerCase()));
      let done = 0, next = 0;
      const worker = async () => {
        while (next < seeds.length && host.isConnected) {
          const track = seeds[next++];
          try {
            let url = track.source_url && /(?:youtube\.com|youtu\.be)/i.test(track.source_url) ? track.source_url : "";
            if (!url) { const found = await API.soft("online.search", { q: `${track.artist || ""} ${track.title || ""}`, limit: 3, source: "ytmusic" }, null); url = found?.results?.[0]?.url || ""; }
            let added = 0;
            if (url) for (const row of (await API.soft("online.related", { url, limit: 12 }, []) || [])) {
              if (added >= 3) break;
              const key = `${row.artist || ""}|${row.title || ""}`.toLowerCase();
              if (row.url && !seen.has(row.url) && !existing.has(key)) { seen.add(row.url); matches.push(row); added++; }
            }
          } catch (_) {}
          done++; if (host.isConnected) status.textContent = L(`Обработано ${done} из ${seeds.length}`, `Processed ${done} of ${seeds.length}`);
        }
      };
      await Promise.all(Array.from({length: Math.min(3, seeds.length)}, worker));
      if (!host.isConnected) return;
      run.disabled = false;
      if (!matches.length) { status.textContent = L("Похожих треков пока не найдено. Попробуйте позже.", "No similar tracks found. Try again later."); return; }
      S.prefs.recommendations = matches.slice(0, 300); savePrefs(); draw(S.prefs.recommendations);
      status.textContent = L(`Найдено: ${matches.length}`, `Found: ${matches.length}`);
    };
  };
})();
