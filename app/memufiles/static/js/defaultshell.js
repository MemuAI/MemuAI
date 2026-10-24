// MemuFiles · Settings → «MemuFiles по умолчанию» (Round 38). Backend: app/memufiles/default_shell.py, /api/default_shell.
// Two opt-in switches (folders, Win+E) — each needs a confirmation; «Вернуть Проводник Windows» restores the backup.
import { getLang } from "./i18n.js";
import { api, h, esc, icon, toast, showError, modal } from "./core.js";

const L = (ru, en) => (getLang() === "en" ? en : ru);

function stateText(state) {
  return state === "on" ? L("включено", "on") : state === "partial" ? L("частично", "partly") : L("выключено", "off");
}

function confirm(text) {
  return new Promise((resolve) => {
    modal({ title: L("MemuFiles по умолчанию", "MemuFiles as default"), icon: "folder", body: h("p", { text }),
      onClose: (value) => resolve(value === true),
      buttons: [{ label: L("Отмена", "Cancel"), cls: "ghost", value: false },
        { label: L("Включить", "Turn on"), cls: "primary", primary: true, value: true }] });
  });
}

/** The settings block (an element); fills itself from /api/default_shell. */
export function defaultShellBlock() {
  const box = h("div", { class: "field" });
  box.append(h("span", { text: L("Интеграция с Windows", "Windows integration") }));
  const list = h("div", { style: { display: "flex", flexDirection: "column", gap: "4px" } });
  const note = h("div", { class: "muted small" });
  box.append(list, note);
  let st = null;
  let busy = false;

  const paint = () => {
    list.textContent = "";
    if (!st) { note.textContent = "…"; return; }
    if (!st.available) { note.textContent = L("Доступно только в Windows.", "Windows only."); return; }
    const f = st.features || {};
    const sw = (feature, label) => {
      const input = h("input", { type: "checkbox" });
      input.checked = (f[feature] || {}).state !== "off";
      input.disabled = busy;
      input.addEventListener("change", () => toggle(feature, input));
      return h("label", { class: "check", style: { margin: "2px 0" } }, input,
        `${label} · ${feature === "hook" ? (st.hook ? L("включено", "on") : L("выключено", "off")) : stateText((f[feature] || {}).state)}`);
    };
    const restore = h("button", { class: "btn sm", html: icon("undo") + esc(L("Вернуть Проводник Windows", "Restore Windows Explorer")) });
    restore.disabled = busy || !st.can_restore;
    restore.addEventListener("click", doRestore);
    const hookInput = sw("hook", L("Запасной перехват Win+E, пока MemuFiles открыт", "Win+E fallback while MemuFiles is open"));
    hookInput.querySelector("input").checked = !!st.hook;
    list.append(sw("folders", L("Открывать папки в MemuFiles", "Open folders in MemuFiles")),
      sw("win_e", L("Win+E открывает MemuFiles", "Win+E opens MemuFiles")), hookInput,
      h("div", { style: { marginTop: "6px" } }, restore));
    note.textContent = L("Двойной щелчок по папкам, «Этот компьютер» и Win+E ведут в MemuFiles. Корзина и Панель управления открываются в Проводнике. Перед изменением реестра сохраняется резервная копия; аварийно — «MemuFiles.exe --restore-explorer» или restore-explorer.reg в папке данных.",
      "Double-clicking folders, This PC and Win+E lead to MemuFiles. The Recycle Bin and Control Panel open in Explorer. The registry is backed up before any change; emergency: «MemuFiles.exe --restore-explorer» or restore-explorer.reg in the data folder.");
  };

  const load = async () => {
    try { st = await api("default_shell"); } catch (err) { st = { available: false }; }
    paint();
  };

  async function toggle(feature, input) {
    const on = input.checked;
    if (on && feature !== "hook") {
      const text = feature === "folders"
        ? L("Папки будут открываться в MemuFiles. Резервная копия реестра сохраняется, отменить можно в любой момент. Включить?", "Folders will open in MemuFiles. The registry is backed up first; you can undo it any time. Turn on?")
        : L("Win+E будет открывать MemuFiles. Резервная копия реестра сохраняется, отменить можно в любой момент. Включить?", "Win+E will open MemuFiles. The registry is backed up first; you can undo it any time. Turn on?");
      if (!(await confirm(text))) { input.checked = false; return; }
    }
    busy = true; paint();
    try {
      st = await api("default_shell", feature === "hook" ? { action: "hook", on }
        : on ? { action: "enable", feature, confirmed: true } : { action: "disable", feature });
      toast(on ? L("Готово — отменить можно здесь же", "Done — undo it right here") : L("Проводник Windows возвращён", "Windows Explorer restored"));
    } catch (err) { showError(err); }
    busy = false;
    await load();
  }

  async function doRestore() {
    busy = true; paint();
    try {
      const r = await api("default_shell", { action: "restore" });
      toast(r.problems && r.problems.length ? L("Восстановлено не всё: ", "Partly restored: ") + r.problems.join("; ") : L("Проводник Windows возвращён", "Windows Explorer restored"));
    } catch (err) { showError(err); }
    busy = false;
    await load();
  }

  load();
  return box;
}
