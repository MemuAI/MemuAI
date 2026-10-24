// A chat with any MemuAI model: streaming answer, Stop, history kept between launches.
const $ = (selector) => document.querySelector(selector);
const log = $("#log"), input = $("#text"), send = $("#send"), picker = $("#model");
let history = Array.isArray(Memu.saved.history) ? Memu.saved.history : [];
let job = null;                       // the running Memu.stream promise (has .cancel())

const escapeHtml = (value) => String(value).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
$("#system").value = Memu.saved.system || "";

// Memu.models() lists media models first: keep only the ones that write text.
async function loadModels() {
  try {
    const rows = (await Memu.models()).filter((row) => !row.image && !row.video && !row.audio);
    picker.innerHTML = rows.map((row) => `<option value="${escapeHtml(row.id)}">${escapeHtml(row.name || row.id)}</option>`).join("");
    const wanted = Memu.saved.model || Memu.manifest.bridge.model;
    if (wanted && rows.some((row) => row.id === wanted)) picker.value = wanted;
    if (!rows.length) picker.innerHTML = `<option value="">${escapeHtml(Memu.t("no_models"))}</option>`;
  } catch (error) {
    picker.innerHTML = `<option value="">${escapeHtml(error.message)}</option>`;
  }
}

function draw() {
  log.innerHTML = history.length
    ? history.map((m, index) => `<div class="mu-msg ${m.role === "user" ? "me" : ""} ${m.error ? "error" : ""} ${m.pending ? "pending" : ""}">${escapeHtml(m.content)}${
        m.role === "assistant" && m.content && !m.pending ? `<button type="button" class="mu-btn ghost small copy" data-copy="${index}">${escapeHtml(Memu.t("copy"))}</button>` : ""}</div>`).join("")
    : `<div class="mu-empty">${escapeHtml(Memu.t("empty"))}</div>`;
  log.scrollTop = log.scrollHeight;
  send.textContent = Memu.t(job ? "stop" : "send");
}

function remember() {
  // Only finished turns are kept, and only the last 40 — Memu.save writes a file each time.
  const clean = history.filter((m) => !m.pending && !m.error).slice(-40);
  Memu.save({ history: clean, model: picker.value, system: $("#system").value }).catch(() => {});
}

async function ask() {
  if (job) { job.cancel(); return; }
  const text = input.value.trim();
  if (!text) return;
  if (!picker.value) { history.push({ role: "assistant", content: Memu.t("choose_model"), error: true }); draw(); return; }
  input.value = "";
  const previous = history.filter((m) => !m.pending && !m.error);
  history.push({ role: "user", content: text });
  const answer = { role: "assistant", content: "", pending: true };
  history.push(answer);
  job = Memu.stream({ model: picker.value, instructions: $("#system").value.trim(), history: previous, text }, {
    onDelta(chunk) { answer.content += chunk; draw(); },
  });
  draw();
  try {
    const result = await job;
    if (!answer.content) answer.content = (result && result.text) || Memu.t("empty_answer");
  } catch (error) {
    answer.content = answer.content ? answer.content + "\n\n" + error.message : error.message;
    answer.error = !answer.content || answer.content === error.message;
  } finally {
    answer.pending = false;
    job = null;
    draw();
    remember();
  }
}

send.addEventListener("click", ask);
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); ask(); }
});
log.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button) return;
  await Memu.copy(history[Number(button.dataset.copy)].content).catch(() => {});
  button.textContent = Memu.t("copied");
});
$("#clear").addEventListener("click", () => { if (job) job.cancel(); history = []; draw(); remember(); });
picker.addEventListener("change", remember);
$("#system").addEventListener("change", remember);

Memu.onLanguage(draw);
draw();
loadModels().then(() => Memu.ready());
