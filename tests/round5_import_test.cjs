
const fs = require("fs");
const path = require("path");
const assert = require("assert");

const source = fs.readFileSync(path.join(__dirname, "..", "app", "pgstatic", "app.js"), "utf8");

function slice(from, to) {
  const start = source.indexOf(from);
  const end = source.indexOf(to);
  assert.ok(start > 0 && end > start, "cannot locate " + from);
  return source.slice(start, end);
}

const importers = slice("function importStamp(", "function importChatsFromFile(");
const facts = slice("function aboutFactsFallback(", "async function aboutFactsFromModel(");
const api = new Function(
  "function trText(ru, en) { return ru; }\n" + importers + facts +
  "\nreturn { parseImportedChats, importedText, importedRole, aboutFactsFallback };")();

const chatgpt = [{
  title: "ChatGPT thread",
  mapping: {
    root: { message: null },
    a: { message: { author: { role: "system" }, create_time: 1, content: { parts: ["ignore me"] } } },
    b: { message: { author: { role: "user" }, create_time: 2, content: { parts: ["Привет"] } } },
    c: { message: { author: { role: "assistant" }, create_time: 3, content: { parts: ["Ответ"] },
                    metadata: { model_slug: "gpt-4o" } } },
  },
}];
const fromChatGpt = api.parseImportedChats(chatgpt);
assert.strictEqual(fromChatGpt.length, 1, "ChatGPT conversation not imported");
assert.strictEqual(fromChatGpt[0].title, "ChatGPT thread");
assert.strictEqual(fromChatGpt[0].messages.length, 2, "system message must be dropped");
assert.strictEqual(fromChatGpt[0].messages[0].role, "user");
assert.strictEqual(fromChatGpt[0].messages[1].role, "assistant");
assert.strictEqual(fromChatGpt[0].messages[1].content, "Ответ");
assert.ok(fromChatGpt[0].messages[1].createdAt > 0, "timestamps must be converted to ms");

const claude = [{
  name: "Claude thread",
  chat_messages: [
    { sender: "human", text: "hi", created_at: "2026-01-01T10:00:00Z" },
    { sender: "assistant", content: [{ type: "text", text: "hello" }] },
  ],
}];
const fromClaude = api.parseImportedChats(claude);
assert.strictEqual(fromClaude.length, 1, "Claude conversation not imported");
assert.strictEqual(fromClaude[0].messages[1].content, "hello");
assert.strictEqual(fromClaude[0].messages[0].role, "user");

const memu = { app: "MemuAI", kind: "memuai-chats", chats: [{
  title: "MemuAI thread",
  messages: [{ role: "user", content: "a" }, { role: "model", content: "b" }],
}] };
const fromMemu = api.parseImportedChats(memu);
assert.strictEqual(fromMemu.length, 1, "MemuAI export not imported");
assert.strictEqual(fromMemu[0].messages[1].role, "assistant", "model role must map to assistant");
assert.ok(fromMemu[0].id.startsWith("imp-"), "imported chats need fresh ids");

assert.deepStrictEqual(api.parseImportedChats({ hello: "world" }), []);
assert.deepStrictEqual(api.parseImportedChats([{ messages: [] }]), []);

const dossier = [
  "1. Демографическая информация",
  "* Пользователя зовут Ахмедуля.",
  "* Доказательство: пользователь сказал \"Зови меня Ахмедуля\". Дата: [01.01.2026].",
  "2. Инструкции",
  "* Отвечать на русском языке.",
].join("\n");
const parsed = api.aboutFactsFallback(dossier);
assert.strictEqual(parsed.length, 2, "evidence lines must be skipped");
assert.ok(parsed[0].includes("Ахмедуля"));
assert.ok(parsed[1].startsWith("Инструкции: "), "facts keep their category");

console.log("round5_import_test: OK");
