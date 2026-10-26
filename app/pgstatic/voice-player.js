

"use strict";
const MemuVoicePlayer = (() => {
  const mounted = new WeakSet();
  let playing = null, menu = null, menuButton = null;
  const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const language = () => typeof PG_LANG !== "undefined" && PG_LANG === "en";
  const words = () => language() ? {play:"Play voice message",pause:"Pause voice message",seek:"Playback position",more:"Voice message options",voice:"Voice message",speed:"Playback speed",download:"Download audio",copy:"Copy transcript",failed:"Audio unavailable · retry",normal:"Normal"}
    : {play:"Воспроизвести голосовое сообщение",pause:"Приостановить голосовое сообщение",seek:"Позиция воспроизведения",more:"Действия с голосовым сообщением",voice:"Голосовое сообщение",speed:"Скорость воспроизведения",download:"Скачать аудио",copy:"Копировать расшифровку",failed:"Аудио недоступно · повторить",normal:"Обычная"};
  const playIcon = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5.5v13l10-6.5z"/></svg>';
  const pauseIcon = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M7 5h4v14H7zm6 0h4v14h-4z"/></svg>';
  function time(seconds, total = false) {
    const n = Math.max(0, Number.isFinite(+seconds) ? (total ? Math.ceil(+seconds) : Math.floor(+seconds)) : 0);
    return Math.floor(n / 60) + ":" + String(n % 60).padStart(2, "0");
  }
  function html(audio, key, transcript = "") {
    const w = words(), length = Math.max(0, +audio.duration || 0);
    const profile = typeof activeChatProfileId === "function" ? activeChatProfileId() : "default";
    const chat = typeof S !== "undefined" ? S.currentId || "" : "";
    return `<div class="voice-message" data-voice-key="${escape(key)}"><div class="voice-player" data-duration="${length}" data-mime="${escape(audio.mime || "")}" data-transcript="${escape(transcript)}" data-profile="${escape(profile)}" data-chat="${escape(chat)}">
      <audio preload="metadata" src="${escape(audio.url)}"></audio>
      <button type="button" class="voice-player-play" aria-label="${w.play}" title="${w.play}">${playIcon}</button>
      <div class="voice-player-main"><div class="voice-player-meta"><span class="voice-player-label">${w.voice}</span><span class="voice-player-time">0:00 / ${time(length,true)}</span></div>
      <input class="voice-player-seek" type="range" min="0" max="${length || 1}" value="0" step="0.01" aria-label="${w.seek}" aria-valuetext="0:00" ${length ? "" : "disabled"}></div>
      <button type="button" class="voice-player-more" aria-label="${w.more}" title="${w.more}" aria-haspopup="menu" aria-expanded="false">⋮</button>
    </div>${audio.truncated ? `<small>${language() ? "Recording limited to three minutes" : "Запись ограничена тремя минутами"}</small>` : ""}</div>`;
  }
  function closeMenu(focus = false) {
    const button = menuButton;
    if (menu) menu.remove();
    menu = null; menuButton = null;
    if (button) { button.setAttribute("aria-expanded", "false"); if (focus && button.isConnected) button.focus(); }
  }
  async function downloadAudio(root, audio) {
    const source = audio.getAttribute("src"), profile = root.dataset.profile, chat = root.dataset.chat;
    const current = () => root.isConnected && audio.getAttribute("src") === source &&
      !(typeof profileSwitching !== "undefined" && profileSwitching) &&
      (typeof activeChatProfileId !== "function" || activeChatProfileId() === profile) &&
      (typeof S === "undefined" || (S.currentId || "") === chat);
    if (!current()) return;
    const url = new URL(source, location.href);
    const clip = url.origin === location.origin && /^\/api\/voice\/clips\/[a-f0-9]{64}\.(wav|mp3|ogg|webm|m4a|flac)$/.test(url.pathname) && url.searchParams.get("profile_id") === profile;
    const temporary = source.startsWith("blob:" + location.origin + "/");
    if (!clip && !temporary) throw new Error(language() ? "Audio unavailable" : "Аудио недоступно");
    const response = await fetch(source, {credentials:"same-origin"});
    if (!current()) return;
    if (!response.ok) throw new Error(language() ? "Could not download audio" : "Не удалось скачать аудио");
    const blob = await response.blob();
    if (!current()) return;
    if (!blob.size || blob.size > 24 * 1024 * 1024) throw new Error(language() ? "Invalid audio recording" : "Некорректная аудиозапись");
    const data = await new Promise((resolve,reject) => {
      const reader = new FileReader(); reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error(language() ? "Could not read audio" : "Не удалось прочитать аудио"));
      reader.readAsDataURL(blob);
    });
    if (!current()) return;
    const byMime = {"audio/mpeg":"mp3","audio/mp3":"mp3","audio/ogg":"ogg","audio/webm":"webm","audio/mp4":"m4a","audio/flac":"flac"};
    const mime = blob.type.split(";")[0] || root.dataset.mime.split(";")[0];
    const name = "MemuAI-voice." + (url.pathname.match(/\.(wav|mp3|ogg|webm|m4a|flac)$/)?.[1] || byMime[mime] || "wav");

    const saved = await saveToDownloads({url:data,name});
    if (!current()) return;
    if (!saved?.ok) throw new Error(language() ? "Could not save audio" : "Не удалось сохранить аудио");
    if (typeof savedToast === "function") savedToast(saved);
    return saved;
  }
  function openMenu(root, audio, button) {
    if (menuButton === button) { closeMenu(true); return; }
    closeMenu(); const w = words();
    menuButton = button; button.setAttribute("aria-expanded", "true");
    menu = document.createElement("div"); menu.className = "voice-player-menu"; menu.setAttribute("role", "menu"); menu.setAttribute("aria-label", w.more);
    menu.innerHTML = `<div class="voice-player-menu-label">${w.speed}</div>` + [0.75,1,1.25,1.5,2].map(rate => `<button type="button" role="menuitemradio" aria-checked="${audio.playbackRate === rate}" data-rate="${rate}"><span>${rate === 1 ? w.normal + " · " : ""}${rate}×</span><span aria-hidden="true">${audio.playbackRate === rate ? "✓" : ""}</span></button>`).join("") + `<hr><button type="button" role="menuitem" data-download>${w.download}<span aria-hidden="true">↓</span></button>${root.dataset.transcript ? `<button type="button" role="menuitem" data-copy>${w.copy}</button>` : ""}`;
    document.body.appendChild(menu);
    const rect = button.getBoundingClientRect(), height = menu.getBoundingClientRect().height;
    menu.style.left = Math.max(8, Math.min(rect.right - 224, window.innerWidth - 232)) + "px";
    menu.style.top = Math.max(8, rect.bottom + height + 8 <= window.innerHeight ? rect.bottom + 6 : rect.top - height - 6) + "px";
    menu.querySelectorAll("[data-rate]").forEach(item => item.onclick = () => { audio.playbackRate = +item.dataset.rate; closeMenu(true); });
    menu.querySelector("[data-download]").onclick = async () => {
      closeMenu(true);
      try { await downloadAudio(root,audio); }
      catch (error) {
        if (root.isConnected && !(typeof profileSwitching !== "undefined" && profileSwitching) &&
            (typeof activeChatProfileId !== "function" || activeChatProfileId() === root.dataset.profile) && typeof toast === "function") toast(error.message || String(error),"warn");
      }
    };
    const copy = menu.querySelector("[data-copy]");
    if (copy) copy.onclick = async () => {
      try { await navigator.clipboard.writeText(root.dataset.transcript); if (typeof toast === "function") toast(language() ? "Transcript copied" : "Расшифровка скопирована"); }
      catch (_) { if (typeof toast === "function") toast(language() ? "Could not copy transcript" : "Не удалось скопировать расшифровку", "warn"); }
      closeMenu(true);
    };
    menu.onkeydown = event => {
      if (event.key === "Escape" || event.key === "Tab") { closeMenu(event.key === "Escape"); return; }
      const items = Array.from(menu.querySelectorAll("button")), current = items.indexOf(document.activeElement);
      let next = null;
      if (event.key === "ArrowDown") next = (current + 1) % items.length;
      if (event.key === "ArrowUp") next = (current - 1 + items.length) % items.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = items.length - 1;
      if (next !== null) { event.preventDefault(); items[next].focus(); }
    };
    menu.querySelector("button").focus();
  }
  function mount(wrapper) {
    if (mounted.has(wrapper)) return;
    mounted.add(wrapper);
    const root = wrapper.querySelector(".voice-player"), audio = root.querySelector("audio"), play = root.querySelector(".voice-player-play"), seek = root.querySelector("input"), more = root.querySelector(".voice-player-more");
    let dragging = false;
    const duration = () => Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : +root.dataset.duration || 0;
    const sync = () => {
      const length = duration(), position = Math.max(0, +audio.currentTime || 0), w = words();
      play.innerHTML = audio.paused ? playIcon : pauseIcon; play.setAttribute("aria-label", audio.paused ? w.play : w.pause); play.title = audio.paused ? w.play : w.pause;
      seek.max = String(length || 1); seek.disabled = !length;
      if (!dragging) seek.value = String(Math.min(position, length));
      seek.style.setProperty("--voice-progress", Math.min(100, length ? position / length * 100 : 0) + "%");
      seek.setAttribute("aria-valuetext", time(position) + " / " + time(length,true));
      root.querySelector(".voice-player-time").textContent = time(position) + " / " + time(length,true);
    };
    play.onclick = async () => {
      if (!audio.paused) { audio.pause(); return; }
      if (playing && playing !== audio) playing.pause();
      playing = audio;
      try {
        if (root.dataset.error) { delete root.dataset.error; root.querySelector(".voice-player-label").textContent = words().voice; audio.load(); }
        if (duration() && audio.currentTime >= duration()) audio.currentTime = 0;
        await audio.play();
      } catch (_) { root.dataset.error = "true"; root.querySelector(".voice-player-label").textContent = words().failed; sync(); }
    };
    for (const event of ["loadedmetadata","durationchange","timeupdate","play","pause","ended","ratechange"]) audio.addEventListener(event, sync);
    audio.addEventListener("error", () => { root.dataset.error = "true"; root.querySelector(".voice-player-label").textContent = words().failed; sync(); });
    seek.onpointerdown = () => { dragging = true; };
    seek.oninput = () => { try { audio.currentTime = +seek.value; } catch (_) {} sync(); };
    seek.onchange = seek.onpointerup = seek.onpointercancel = () => { dragging = false; sync(); };
    seek.onkeydown = event => {
      if (!["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) return;
      event.preventDefault(); const length = duration();
      try { audio.currentTime = event.key === "Home" ? 0 : event.key === "End" ? length : Math.max(0, Math.min(length, audio.currentTime + (event.key === "ArrowLeft" ? -5 : 5))); } catch (_) {}
      sync();
    };
    more.onclick = () => openMenu(root, audio, more);
    more.onkeydown = event => { if (event.key === "ArrowDown") { event.preventDefault(); openMenu(root,audio,more); } };
    sync();
  }
  function stash(box) {
    closeMenu(); const saved = new Map();
    box.querySelectorAll(".voice-message[data-voice-key]").forEach(root => {
      const key = root.dataset.voiceKey + "|" + root.querySelector("audio").getAttribute("src");
      if (!saved.has(key)) saved.set(key, []); saved.get(key).push(root);
    });
    return saved;
  }
  function hydrate(box, saved = new Map()) {
    box.querySelectorAll(".voice-message[data-voice-key]").forEach(root => {
      const key = root.dataset.voiceKey + "|" + root.querySelector("audio").getAttribute("src"), existing = saved.get(key)?.shift();
      if (existing) {
        for (const field of ["transcript","profile","chat"]) existing.querySelector(".voice-player").dataset[field] = root.querySelector(".voice-player").dataset[field];
        root.replaceWith(existing);
      } else mount(root);
    });
    saved.forEach(rows => rows.forEach(root => { const audio = root.querySelector("audio"); audio.pause(); if (playing === audio) playing = null; }));
  }
  document.addEventListener("pointerdown", event => { if (menu && !menu.contains(event.target) && !menuButton?.contains(event.target)) closeMenu(); });
  window.addEventListener("resize", () => closeMenu()); window.addEventListener("scroll", () => closeMenu(), true);
  return { html, stash, hydrate, closeMenu };
})();
