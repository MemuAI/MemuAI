/* Planning → «Сценарий»: a visual editor for every scenario step of app/plan_scenarios.py.
   Steps are edited in place inside the builder draft (st.draft.scenario); generic inputs carry
   data-path and are written by plan.js, structural actions are data-act="scen-*" buttons. */
(function () {
  "use strict";
  const en = () => (typeof PG_LANG !== "undefined" && PG_LANG === "en");
  const T = (ru, eng) => (en() ? eng : ru);
  const H = (value) => (typeof esc === "function" ? esc(value) : String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])));
  const I = (name) => (typeof icon === "function" ? icon(name) : "");
  const $q = (sel, root = document) => root.querySelector(sel);
  const CONTAINERS = { if: ["then", "else"], repeat: ["steps"], foreach: ["steps"], try: ["steps", "catch"], macro: ["steps"] };
  const DESTRUCTIVE = ["file_create", "file_edit", "run", "file_copy", "file_move", "file_delete", "folder_create", "csv_append", "zip"];
  const MAX_DEPTH = 4;
  let lastTest = null;
  let lastFocus = null;

  /* ------------------------------------------------------------------ catalog */
  const CATS = () => [
    ["data", "edit", T("Переменные и данные", "Variables and data")],
    ["files", "folder", T("Файлы и папки", "Files and folders")],
    ["logic", "branch", T("Логика и порядок", "Logic and flow")],
    ["net", "globe", T("Сеть и ИИ", "Web and AI")],
    ["msg", "bell", T("Сообщения", "Messages")],
    ["app", "grid", T("MemuAI и система", "MemuAI and system")],
  ];
  const opt = (pairs) => pairs.map(([v, ru, eng]) => [v, eng === undefined ? ru : T(ru, eng)]);
  const SCOPE = () => opt([["run", "только в этом запуске", "this run only"], ["task", "сохранить для задачи", "keep for the task"], ["global", "общая (для всех задач)", "global (all tasks)"]]);
  const COND_OPS = () => opt([["eq", "="], ["ne", "≠"], ["gt", ">"], ["ge", "≥"], ["lt", "<"], ["le", "≤"],
    ["contains", "содержит", "contains"], ["not_contains", "не содержит", "does not contain"], ["starts_with", "начинается с", "starts with"],
    ["ends_with", "заканчивается на", "ends with"], ["regex", "совпадает с regex", "matches regex"], ["empty", "пусто", "is empty"],
    ["not_empty", "не пусто", "is not empty"], ["is_number", "число", "is a number"], ["is_true", "истина", "is true"],
    ["is_false", "ложь", "is false"], ["exists", "переменная есть", "variable exists"], ["in_list", "есть в списке", "is in the list"]]);
  const NO_RIGHT = ["empty", "not_empty", "is_number", "is_true", "is_false", "exists"];

  // Field kinds: text, area, code (monospace), num, bool, sel, var (a variable name), cond.
  const f = (k, t, ru, eng, extra = {}) => ({ k, t, label: () => T(ru, eng), ...extra });
  const nameF = (def) => f("name", "var", "Сохранить в переменную", "Save into variable", { ph: def || "result", def: def || "" });
  const scopeF = () => f("scope", "sel", "Хранить", "Keep", { opts: SCOPE, def: "run" });
  const confirmF = (def = false) => f("require_confirm", "bool", "Требовать подтверждение при сохранении правила", "Require confirmation when the rule is saved", { def });
  const pathF = (ru = "Файл", eng = "File", ph = "notes/note.txt") => f("path", "text", ru, eng, { ph, wide: true, req: true });
  const TEXT_HINT = { replace: [["Найти", "Find"], ["Заменить на", "Replace with"]], regex_replace: [["Шаблон regex", "Regex pattern"], ["Замена (\\1 — группа)", "Replacement (\\1 = group)"]],
    regex_find: [["Шаблон regex", "Regex pattern"]], regex_all: [["Шаблон regex", "Regex pattern"]], slice: [["С позиции", "From index"], ["До позиции", "To index"]],
    split: [["Разделитель", "Delimiter"], ["Не обрезать пробелы (любой текст)", "Keep spaces (any text)"]], contains: [["Искомый текст", "Text to find"]],
    starts_with: [["Начало", "Prefix"]], ends_with: [["Окончание", "Suffix"]], pad: [["Ширина", "Width"], ["Символ", "Character"]], trim: [["Какие символы срезать (необязательно)", "Characters to strip (optional)"]] };
  const DATE_VALUE = ["format", "add", "diff_days", "diff_minutes", "weekday", "start_of_week", "start_of_month", "parse", "timestamp"];
  const LIST_VALUE = ["push", "unshift", "remove", "contains", "join", "from_text"];

  const SCHEMA = () => ({
    set: { cat: "data", icon: "edit", desc: T("Создать или изменить переменную: присвоить, +1, прибавить, дописать…", "Create or change a variable: set, +1, add, append…"), fields: [
      f("name", "var", "Переменная", "Variable", { ph: "count", req: true }),
      f("op", "sel", "Операция", "Operation", { rerender: true, def: "set", opts: () => opt([["set", "присвоить", "set"], ["inc", "+ (увеличить)", "+ (increase)"], ["dec", "− (уменьшить)", "− (decrease)"],
        ["add", "прибавить число", "add a number"], ["mul", "умножить", "multiply"], ["append", "дописать текст", "append text"], ["toggle", "переключить да/нет", "toggle yes/no"], ["delete", "удалить", "delete"]]) }),
      f("value", "area", "Значение", "Value", { show: (s) => ["set", "append"].includes(s.op || "set"), ph: "${today}" }),
      f("by", "num", "На сколько", "By", { show: (s) => ["inc", "dec", "add", "mul"].includes(s.op), def: 1, min: -1e12, max: 1e12 }),
      scopeF()] },
    calc: { cat: "data", icon: "hash", desc: T("Безопасная арифметика: + − * / % **, round, min, max, abs, len", "Safe arithmetic: + − * / % **, round, min, max, abs, len"), fields: [
      f("name", "var", "Результат в", "Result into", { ph: "total", req: true }), f("expr", "code", "Выражение", "Expression", { ph: "count * 2 + 1", req: true }),
      f("decimals", "num", "Знаков после запятой", "Decimal places", { min: 0, max: 10, optional: true }), scopeF()] },
    text: { cat: "data", icon: "text", desc: T("Обработка текста: регистр, замена, regex, разбиение, хэш, base64, шаблон…", "Text processing: case, replace, regex, split, hash, base64, template…"), fields: [
      nameF("text"), f("op", "sel", "Действие", "Operation", { rerender: true, def: "trim", opts: () => opt([["trim", "обрезать пробелы", "trim"], ["upper", "ВЕРХНИЙ регистр", "UPPER case"], ["lower", "нижний регистр", "lower case"], ["title", "С Заглавных", "Title Case"],
        ["replace", "заменить", "replace"], ["regex_replace", "заменить regex", "regex replace"], ["regex_find", "найти regex (первое)", "regex find (first)"], ["regex_all", "найти regex (все)", "regex find (all)"],
        ["slice", "вырезать часть", "slice"], ["length", "длина", "length"], ["split", "разбить в список", "split into a list"], ["lines", "строки → список", "lines → list"], ["words", "слова → список", "words → list"],
        ["first_line", "первая строка", "first line"], ["last_line", "последняя строка", "last line"], ["contains", "содержит?", "contains?"], ["starts_with", "начинается с?", "starts with?"], ["ends_with", "заканчивается на?", "ends with?"],
        ["pad", "дополнить слева", "pad left"], ["number", "в число", "to a number"], ["template", "шаблон с ${…}", "template with ${…}"], ["url_encode", "URL-кодировать", "URL encode"], ["url_decode", "URL-декодировать", "URL decode"],
        ["base64_encode", "в base64", "to base64"], ["base64_decode", "из base64", "from base64"], ["sha256", "SHA-256", "SHA-256"], ["md5", "MD5", "MD5"]]) }),
      f("input", "area", "Исходный текст", "Input text", { ph: "${content}" }),
      f("arg1", "text", "Аргумент 1", "Argument 1", { show: (s) => !!TEXT_HINT[s.op || "trim"], labelFor: (s) => (TEXT_HINT[s.op || "trim"] || [])[0] }),
      f("arg2", "text", "Аргумент 2", "Argument 2", { show: (s) => ((TEXT_HINT[s.op || "trim"] || []).length > 1), labelFor: (s) => (TEXT_HINT[s.op || "trim"] || [])[1] }),
      scopeF()] },
    random: { cat: "data", icon: "shuffle", desc: T("Случайное число, выбор из вариантов, перемешать, UUID, пароль", "A random number, a pick, a shuffle, a UUID, a password"), fields: [
      nameF("random"), f("mode", "sel", "Что получить", "What", { rerender: true, def: "int", opts: () => opt([["int", "целое число", "integer"], ["float", "дробное число", "float"], ["pick", "один из вариантов", "pick one"], ["shuffle", "перемешать варианты", "shuffle"], ["uuid", "UUID", "UUID"], ["password", "пароль", "password"], ["bool", "да / нет", "yes / no"]]) }),
      f("min", "num", "От", "From", { show: (s) => ["int", "float"].includes(s.mode || "int"), def: 1, min: -1e12, max: 1e12 }),
      f("max", "num", "До", "To", { show: (s) => ["int", "float"].includes(s.mode || "int"), def: 100, min: -1e12, max: 1e12 }),
      f("items", "area", "Варианты (по строке или через запятую)", "Options (one per line or comma-separated)", { show: (s) => ["pick", "shuffle"].includes(s.mode), req: true }),
      f("length", "num", "Длина пароля", "Password length", { show: (s) => s.mode === "password", def: 12, min: 4, max: 64 }), scopeF()] },
    date: { cat: "data", icon: "calendar", desc: T("Сейчас, сегодня, формат, прибавить дни, разница дат, начало недели/месяца", "Now, today, format, add days, date difference, start of week/month"), fields: [
      nameF("date"), f("mode", "sel", "Что получить", "What", { rerender: true, def: "now", opts: () => opt([["now", "сейчас", "now"], ["today", "сегодня", "today"], ["timestamp", "отметка времени (сек)", "timestamp (s)"], ["weekday", "день недели", "weekday"],
        ["format", "отформатировать дату", "format a date"], ["add", "прибавить к дате", "add to a date"], ["diff_days", "разница в днях", "difference in days"], ["diff_minutes", "разница в минутах", "difference in minutes"],
        ["start_of_week", "начало недели", "start of week"], ["start_of_month", "начало месяца", "start of month"], ["parse", "распознать дату", "parse a date"]]) }),
      f("value", "text", "Дата (пусто — сейчас)", "Date (empty = now)", { show: (s) => DATE_VALUE.includes(s.mode), ph: "${today}" }),
      f("value2", "text", "Вторая дата", "Second date", { show: (s) => ["diff_days", "diff_minutes"].includes(s.mode), ph: "2026-12-31" }),
      f("amount", "num", "Сколько", "Amount", { show: (s) => s.mode === "add", def: 1, min: -100000, max: 100000 }),
      f("unit", "sel", "Единица", "Unit", { show: (s) => s.mode === "add", def: "days", opts: () => opt([["minutes", "минут", "minutes"], ["hours", "часов", "hours"], ["days", "дней", "days"], ["weeks", "недель", "weeks"]]) }),
      f("format", "text", "Формат (YYYY-MM-DD HH:mm)", "Format (YYYY-MM-DD HH:mm)", { show: (s) => !["timestamp", "weekday", "diff_days", "diff_minutes"].includes(s.mode), ph: "DD.MM.YYYY" }), scopeF()] },
    json: { cat: "data", icon: "code", desc: T("Разобрать JSON, взять поле по пути a.b.0, изменить, объединить", "Parse JSON, read a.b.0, change, merge"), fields: [
      nameF("data"), f("op", "sel", "Действие", "Operation", { rerender: true, def: "get", opts: () => opt([["get", "взять поле", "get a field"], ["set", "задать поле", "set a field"], ["delete", "удалить поле", "delete a field"], ["parse", "разобрать текст", "parse text"], ["stringify", "в текст JSON", "to JSON text"], ["keys", "ключи", "keys"], ["length", "размер", "length"], ["merge", "объединить", "merge"]]) }),
      f("input", "code", "Источник (JSON или ${var})", "Source (JSON or ${var})", { ph: "${response}" }),
      f("path", "text", "Путь", "Path", { show: (s) => ["get", "set", "delete"].includes(s.op || "get"), ph: "items.0.title" }),
      f("value", "code", "Значение", "Value", { show: (s) => ["set", "merge"].includes(s.op) }), scopeF()] },
    list: { cat: "data", icon: "list", desc: T("Список: добавить, взять, удалить, уникальные, сортировка, сумма, склеить", "A list: push, get, remove, unique, sort, sum, join"), fields: [
      f("name", "var", "Список", "List", { ph: "items", req: true }),
      f("op", "sel", "Действие", "Operation", { rerender: true, def: "push", opts: () => opt([["push", "добавить в конец", "push"], ["unshift", "добавить в начало", "unshift"], ["pop", "взять последний", "pop"], ["shift", "взять первый", "shift"],
        ["get", "элемент по номеру", "item by index"], ["remove", "удалить значение", "remove a value"], ["contains", "есть ли значение", "contains"], ["length", "длина", "length"], ["unique", "только уникальные", "unique"], ["sort", "сортировать", "sort"],
        ["reverse", "перевернуть", "reverse"], ["join", "склеить в текст", "join into text"], ["slice", "часть списка", "slice"], ["sum", "сумма", "sum"], ["min", "минимум", "min"], ["max", "максимум", "max"], ["clear", "очистить", "clear"], ["from_text", "из текста (строки)", "from text (lines)"]]) }),
      f("value", "area", "Значение", "Value", { show: (s) => LIST_VALUE.includes(s.op || "push"), labelFor: (s) => (s.op === "join" ? [T("Разделитель", "Delimiter"), ""] : null) }),
      f("index", "num", "Номер (с 0, −1 — последний)", "Index (from 0, −1 = last)", { show: (s) => ["get", "slice"].includes(s.op), def: 0, min: -2000, max: 2000 }),
      f("count", "num", "Сколько", "Count", { show: (s) => s.op === "slice", def: 0, min: 0, max: 2000 }),
      f("into", "var", "Результат в (необязательно)", "Result into (optional)", { ph: "picked" }), scopeF()] },

    file_create: { cat: "files", icon: "file", destructive: true, desc: T("Создать текстовый файл внутри папки сценария", "Create a text file inside the scenario folder"), fields: [
      pathF("Файл", "File", "notes/${today}.md"), f("content", "area", "Содержимое", "Content", { rows: 4 }), f("overwrite", "bool", "Перезаписать, если файл уже есть", "Overwrite if the file exists"), confirmF()] },
    file_edit: { cat: "files", icon: "edit", destructive: true, desc: T("Заменить текст, regex, дописать в начало/конец или перезаписать", "Replace text, regex, prepend/append or overwrite"), fields: [
      pathF(), f("mode", "sel", "Как изменить", "How", { rerender: true, def: "replace_first", opts: () => opt([["replace_first", "заменить первое", "replace first"], ["replace_all", "заменить все", "replace all"], ["regex", "заменить по regex", "regex replace"], ["append", "дописать в конец", "append"], ["prepend", "дописать в начало", "prepend"], ["overwrite", "перезаписать целиком", "overwrite"]]) }),
      f("find", "area", "Найти", "Find", { show: (s) => ["replace_first", "replace_all", "regex"].includes(s.mode || "replace_first"), req: true }),
      f("replace", "area", "Заменить на", "Replace with", { show: (s) => ["replace_first", "replace_all", "regex"].includes(s.mode || "replace_first") }),
      f("append", "area", "Текст", "Text", { show: (s) => ["append", "prepend", "overwrite"].includes(s.mode) }),
      f("create", "bool", "Создать файл, если его нет", "Create the file if missing"), confirmF()] },
    file_read: { cat: "files", icon: "book", desc: T("Прочитать файл в переменную: текст, строки или JSON", "Read a file into a variable: text, lines or JSON"), fields: [
      pathF(), nameF("content"), f("as", "sel", "Как", "As", { def: "text", opts: () => opt([["text", "текст", "text"], ["lines", "список строк", "list of lines"], ["json", "JSON", "JSON"]]) }),
      f("max_chars", "num", "Не больше символов", "At most characters", { def: 100000, min: 100, max: 1000000 }), f("missing_ok", "bool", "Нет файла — не ошибка", "Missing file is not an error")] },
    file_info: { cat: "files", icon: "search", desc: T("Есть ли файл, размер, дата изменения → ${file.exists}, ${file.size}", "Does the file exist, its size and date → ${file.exists}, ${file.size}"), fields: [pathF(), nameF("file")] },
    file_list: { cat: "files", icon: "folder", desc: T("Список файлов по маске → переменная-список (для «Для каждого»)", "Files by mask → a list variable (for “For each”)"), fields: [
      pathF("Папка", "Folder", "."), f("pattern", "text", "Маска", "Mask", { ph: "*.md", def: "*" }), f("recursive", "bool", "Включая подпапки", "Include subfolders"), f("dirs", "bool", "Только папки", "Folders only"),
      f("sort", "sel", "Сортировка", "Sort", { def: "name", opts: () => opt([["name", "по имени", "by name"], ["modified", "по дате изменения", "by modified"], ["size", "по размеру", "by size"]]) }),
      f("limit", "num", "Не больше", "At most", { def: 100, min: 1, max: 500 }), nameF("files")] },
    file_copy: { cat: "files", icon: "copy", destructive: true, desc: T("Скопировать файл внутри папки сценария", "Copy a file inside the scenario folder"), fields: [pathF("Откуда", "From"), f("to", "text", "Куда", "To", { ph: "backup/note.txt", wide: true, req: true }), f("overwrite", "bool", "Перезаписать", "Overwrite")] },
    file_move: { cat: "files", icon: "forward", destructive: true, desc: T("Переместить или переименовать файл", "Move or rename a file"), fields: [pathF("Откуда", "From"), f("to", "text", "Куда", "To", { ph: "archive/note.txt", wide: true, req: true }), f("overwrite", "bool", "Перезаписать", "Overwrite")] },
    file_delete: { cat: "files", icon: "trash", destructive: true, desc: T("Удалить файл (только внутри папки сценария)", "Delete a file (only inside the scenario folder)"), fields: [pathF(), f("missing_ok", "bool", "Нет файла — не ошибка", "Missing file is not an error", { def: true })] },
    folder_create: { cat: "files", icon: "folder", destructive: true, desc: T("Создать папку (и вложенные)", "Create a folder (with parents)"), fields: [pathF("Папка", "Folder", "reports/${today}")] },
    csv_append: { cat: "files", icon: "table", destructive: true, desc: T("Добавить строку в CSV-таблицу (значения через |)", "Append a row to a CSV table (values separated by |)"), fields: [
      pathF("CSV-файл", "CSV file", "data/log.csv"), f("values", "text", "Значения через |", "Values separated by |", { ph: "${today}|${count}|ok", wide: true, req: true }),
      f("header", "text", "Заголовок для нового файла", "Header for a new file", { ph: "date|count|status", wide: true }),
      f("delimiter", "sel", "Разделитель", "Delimiter", { def: ";", opts: () => [[";", ";"], [",", ","], ["tab", "Tab"]] })] },
    zip: { cat: "files", icon: "archive", destructive: true, desc: T("Упаковать файл или папку в ZIP", "Pack a file or a folder into a ZIP"), fields: [pathF("Что упаковать", "What to pack", "notes"), f("to", "text", "Архив", "Archive", { ph: "backups/notes-${today}.zip", wide: true, req: true }), f("overwrite", "bool", "Перезаписать архив", "Overwrite the archive", { def: true })] },

    if: { cat: "logic", icon: "split", desc: T("Ветвление: если условие — одни шаги, иначе — другие", "Branching: if the condition holds — these steps, else — those"), fields: [f("cond", "cond", "Условие", "Condition")] },
    repeat: { cat: "logic", icon: "retry", desc: T("Повторить шаги N раз или пока выполняется условие; ${loop.index} — номер", "Repeat steps N times or while a condition holds; ${loop.index} is the number"), fields: [
      f("times", "num", "Повторов (0 — пока условие)", "Times (0 = while the condition)", { def: 3, min: 0, max: 20 }), f("while", "cond", "Пока (необязательно)", "While (optional)", { optional: true })] },
    foreach: { cat: "logic", icon: "list", desc: T("Для каждого элемента списка или строки текста выполнить шаги", "Run steps for every list item or text line"), fields: [
      f("items", "area", "Что перебирать", "Iterate over", { ph: "${files}", req: true }), f("as", "var", "Имя элемента", "Item name", { def: "item", ph: "item" }), f("limit", "num", "Не больше", "At most", { def: 50, min: 1, max: 200 })] },
    try: { cat: "logic", icon: "shield", desc: T("Попробовать шаги; при ошибке выполнить «Если ошибка» (${error})", "Try steps; on failure run “On error” (${error})"), fields: [] },
    wait: { cat: "logic", icon: "hourglass", desc: T("Пауза до 300 секунд", "A pause of up to 300 seconds"), fields: [f("seconds", "num", "Секунд", "Seconds", { def: 5, min: 1, max: 300 })] },
    stop: { cat: "logic", icon: "flag", desc: T("Завершить сценарий успешно или с ошибкой", "Finish the scenario as a success or a failure"), fields: [
      f("status", "sel", "Итог", "Result", { def: "ok", opts: () => opt([["ok", "успешно", "success"], ["fail", "с ошибкой", "failure"]]) }), f("text", "text", "Текст итога", "Result text", { wide: true })] },
    log: { cat: "logic", icon: "book", desc: T("Строка в итог и журнал запуска", "A line in the result and the run log"), fields: [f("text", "area", "Текст", "Text", { req: true, ph: "count = ${count}" })] },
    macro: { cat: "logic", icon: "layout", desc: T("Группа шагов с названием — для порядка", "A named group of steps — to keep things tidy"), fields: [f("name", "text", "Название группы", "Group name", { ph: T("Подготовка", "Preparation") })] },
    macro_call: { cat: "logic", icon: "zap", desc: T("Вызвать сохранённый макрос из библиотеки", "Call a saved macro from the library"), fields: [f("macro_id", "macro", "Макрос", "Macro", { req: true })] },

    http: { cat: "net", icon: "globe", desc: T("HTTP-запрос к API; ответ JSON → переменная", "An HTTP request to an API; the JSON answer → a variable"), fields: [
      f("method", "sel", "Метод", "Method", { rerender: true, def: "GET", opts: () => ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"].map((m) => [m, m]) }),
      f("url", "text", "Адрес", "URL", { ph: "https://api.example.com/items", wide: true, req: true }), f("headers_text", "code", "Заголовки (JSON)", "Headers (JSON)", { ph: '{"Accept": "application/json"}', rows: 2 }),
      f("body", "code", "Тело запроса", "Request body", { show: (s) => !["GET", "HEAD"].includes(s.method || "GET") }),
      f("parse", "sel", "Ответ", "Response", { def: "auto", opts: () => opt([["auto", "авто", "auto"], ["json", "JSON", "JSON"], ["text", "текст", "text"]]) }),
      f("timeout", "num", "Таймаут, с", "Timeout, s", { def: 20, min: 1, max: 60 }), nameF("response")] },
    web_search: { cat: "net", icon: "search", desc: T("Веб-поиск → список результатов (title, url, snippet)", "Web search → a list of results (title, url, snippet)"), fields: [
      f("query", "text", "Запрос", "Query", { ph: T("новости ИИ ${today}", "AI news ${today}"), wide: true, req: true }), f("limit", "num", "Результатов", "Results", { def: 5, min: 1, max: 10 }), nameF("results")] },
    ai: { cat: "net", icon: "sparkle", desc: T("Спросить модель; ответ → переменная (можно JSON)", "Ask a model; the answer → a variable (JSON possible)"), fields: [
      f("prompt", "area", "Запрос", "Prompt", { rows: 4, req: true, ph: T("Перескажи кратко: ${content}", "Summarize briefly: ${content}") }), f("system", "area", "Системная инструкция (необязательно)", "System instruction (optional)", { rows: 2 }),
      f("model", "model", "Модель", "Model"), f("json", "bool", "Ответ — JSON", "Answer as JSON"), nameF("answer"), scopeF()] },
    integration: { cat: "net", icon: "link", desc: T("Действие в Telegram или Chrome; изменения — по разрешению в настройках", "A Telegram or Chrome action; changes need the permission in settings"), fields: [
      f("tool", "tool", "Действие", "Action", { req: true }), f("args_text", "code", "Аргументы (JSON, можно ${var})", "Arguments (JSON, may use ${var})", { rows: 3, def: "{}" }), f("name", "var", "Результат в (необязательно)", "Result into (optional)")] },

    notify: { cat: "msg", icon: "message", desc: T("Строка итога запуска (уходит в уведомление/Telegram по настройкам задачи)", "A result line (goes to the notification/Telegram per task settings)"), fields: [f("text", "area", "Текст", "Text", { req: true })] },
    alert: { cat: "msg", icon: "bell", desc: T("Отдельное уведомление Windows и запись во «Входящие»", "A separate Windows notification and an Inbox entry"), fields: [
      f("title", "text", "Заголовок", "Title", { wide: true }), f("text", "area", "Текст", "Text", { req: true }), f("windows", "bool", "Уведомление Windows", "Windows notification", { def: true }), f("inbox", "bool", "Во «Входящие»", "To the Inbox", { def: true })] },
    telegram: { cat: "msg", icon: "send", desc: T("Сообщение в ваш Telegram-бот MemuAI", "A message to your MemuAI Telegram bot"), fields: [f("text", "area", "Текст", "Text", { req: true }), f("silent", "bool", "Без звука", "Silent")] },

    memubase: { cat: "app", icon: "database", desc: T("MemuBase: добавить строку, найти строки, посчитать", "MemuBase: insert a row, query rows, count"), fields: [
      f("op", "sel", "Действие", "Operation", { rerender: true, def: "insert", opts: () => opt([["insert", "добавить строку", "insert a row"], ["query", "найти строки", "query rows"], ["count", "посчитать", "count"]]) }),
      f("table", "text", "Таблица", "Table", { ph: "notes", req: true }), f("row", "code", "Строка (JSON или поле=значение)", "Row (JSON or field=value)", { show: (s) => (s.op || "insert") === "insert", req: true }),
      f("unique", "text", "Уникальное поле (без дублей)", "Unique field (no duplicates)", { show: (s) => (s.op || "insert") === "insert", ph: "url" }),
      f("search", "text", "Поиск", "Search", { show: (s) => s.op === "query" || s.op === "count" }), f("limit", "num", "Не больше", "At most", { show: (s) => s.op === "query", def: 50, min: 1, max: 500 }), nameF("rows")] },
    memory: { cat: "app", icon: "brain", desc: T("Сохранить факт в память MemuAI", "Save a fact into MemuAI memory"), fields: [f("text", "area", "Факт", "Fact", { req: true }), f("category", "text", "Категория", "Category", { ph: T("работа", "work") })] },
    task: { cat: "app", icon: "clock", desc: T("Запустить, включить или выключить другую задачу планирования", "Run, enable or disable another scheduled task"), fields: [
      f("op", "sel", "Действие", "Operation", { def: "run", opts: () => opt([["run", "запустить", "run"], ["enable", "включить", "enable"], ["disable", "выключить", "disable"], ["toggle", "переключить", "toggle"]]) }), f("task_id", "task", "Задача", "Task", { req: true })] },
    module: { cat: "app", icon: "grid", desc: T("Реальное действие модуля: экспорт, резервная копия, отчёт, ИИ-сводка…", "A real module action: export, backup, report, AI digest…"), fields: [f("module", "module", "Модуль", "Module", { rerender: true, req: true }), f("action", "maction", "Действие", "Action", { req: true })] },
    command: { cat: "app", icon: "zap", desc: T("Ваша команда быстрого доступа (окно MemuAI должно быть открыто)", "Your Quick Access command (the MemuAI window must be open)"), fields: [f("command", "command", "Команда", "Command", { req: true })] },
    assistant: { cat: "app", icon: "bot", desc: T("Действие ассистента по имени с параметрами", "An assistant action by name with parameters"), fields: [f("action", "text", "Действие", "Action", { req: true, ph: "volume" }), f("params_text", "code", "Параметры (JSON)", "Parameters (JSON)", { def: "{}", rows: 2 })] },
    run: { cat: "app", icon: "play", destructive: true, desc: T("Запустить программу из белого списка (без shell, с таймаутом)", "Run an allowlisted program (no shell, with a timeout)"), fields: [
      f("argv_text", "text", "Команда", "Command", { ph: "notepad.exe notes/${today}.md", wide: true, req: true }), f("cwd", "text", "Рабочая папка (необязательно)", "Working folder (optional)", { wide: true }),
      f("timeout", "num", "Таймаут, с", "Timeout, s", { def: 30, min: 1, max: 300 }), f("name", "var", "Вывод в переменную (необязательно)", "Output into variable (optional)"), confirmF(true)] },
    system: { cat: "app", icon: "monitor", desc: T("Состояние компьютера: процессор, память, диск, батарея, программа…", "Computer state: CPU, memory, disk, battery, a program…"), fields: [
      nameF("value"), f("metric", "sel", "Что узнать", "What", { rerender: true, def: "cpu_pct", opts: () => opt([["cpu_pct", "загрузка процессора, %", "CPU load, %"], ["ram_pct", "занято памяти, %", "memory used, %"], ["ram_free_gb", "свободно памяти, ГБ", "free memory, GB"],
        ["disk_free_gb", "свободно на диске, ГБ", "free disk, GB"], ["disk_free_pct", "свободно на диске, %", "free disk, %"], ["battery_pct", "заряд батареи, %", "battery, %"], ["plugged", "на зарядке?", "plugged in?"], ["uptime_hours", "работает часов", "uptime, hours"],
        ["process", "запущена ли программа", "is a program running"], ["processes", "число процессов", "process count"], ["hostname", "имя компьютера", "host name"], ["os", "система", "OS"], ["user", "пользователь", "user"], ["time", "время", "time"]]) }),
      f("arg", "text", "Программа / диск", "Program / disk", { show: (s) => ["process", "disk_free_gb", "disk_free_pct"].includes(s.metric), ph: "chrome.exe" })] },
    open_url: { cat: "app", icon: "external", desc: T("Открыть ссылку в браузере", "Open a link in the browser"), fields: [f("url", "text", "Адрес", "URL", { ph: "https://", wide: true, req: true })] },
  });
  const TITLES = () => ({ set: T("Переменная", "Variable"), calc: T("Вычислить", "Calculate"), text: T("Текст", "Text"), random: T("Случайное значение", "Random value"),
    date: T("Дата и время", "Date and time"), json: "JSON", list: T("Список", "List"), file_create: T("Создать файл", "Create a file"), file_edit: T("Изменить файл", "Edit a file"),
    file_read: T("Прочитать файл", "Read a file"), file_info: T("Проверить файл", "Check a file"), file_list: T("Список файлов", "List files"), file_copy: T("Копировать файл", "Copy a file"),
    file_move: T("Переместить файл", "Move a file"), file_delete: T("Удалить файл", "Delete a file"), folder_create: T("Создать папку", "Create a folder"), csv_append: T("Строка в CSV", "Row to CSV"),
    zip: T("Архив ZIP", "ZIP archive"), if: T("Если…", "If…"), repeat: T("Повторить", "Repeat"), foreach: T("Для каждого", "For each"), try: T("Попробовать", "Try"), wait: T("Подождать", "Wait"),
    stop: T("Завершить", "Stop"), log: T("Запись в журнал", "Log entry"), macro: T("Группа шагов", "Step group"), macro_call: T("Макрос", "Macro"), run: T("Запустить программу", "Run a program"),
    system: T("Состояние системы", "System state"), open_url: T("Открыть ссылку", "Open a link"), integration: T("Telegram или Chrome", "Telegram or Chrome"), http: T("HTTP-запрос", "HTTP request"),
    telegram: "Telegram", notify: T("Строка итога", "Result line"), alert: T("Уведомление", "Notification"), ai: T("Спросить модель", "Ask a model"), web_search: T("Веб-поиск", "Web search"),
    memubase: "MemuBase", memory: T("Запомнить", "Remember"), task: T("Другая задача", "Another task"), module: T("Действие модуля", "Module action"),
    command: T("Команда быстрого доступа", "Quick Access command"), assistant: T("Действие ассистента", "Assistant action") });
  const title = (type) => TITLES()[type] || type;

  /* ------------------------------------------------------------------ data helpers */
  function getPath(obj, path) { return String(path).split(".").reduce((o, k) => (o == null ? o : o[k]), obj); }
  function fresh(type) {
    const spec = SCHEMA()[type];
    const step = { type };
    for (const fd of (spec?.fields || [])) {
      if (fd.def !== undefined && fd.def !== "") step[fd.k] = fd.def;
      else if (fd.t === "bool") step[fd.k] = false;
      else if (fd.t === "cond" && !fd.optional) step[fd.k] = { left: "", op: "eq", right: "" };
    }
    if (type === "set") { step.name = "count"; step.value = ""; }
    if (type === "run") step.argv_text = "";
    for (const key of CONTAINERS[type] || []) step[key] = [];
    if (type === "repeat") step.times = 3;
    return step;
  }
  /** Server steps → editor steps (UI-only text fields for lists/objects). */
  function load(steps) {
    return (Array.isArray(steps) ? steps : []).map((raw) => {
      const s = { ...raw };
      if (s.type === "run" && s.argv_text === undefined) s.argv_text = (s.argv || []).map((a) => (/\s/.test(a) ? `"${a}"` : a)).join(" ");
      if (s.type === "integration" && s.args_text === undefined) s.args_text = JSON.stringify(s.args || {}, null, 2);
      if (s.type === "http" && s.headers_text === undefined) s.headers_text = Object.keys(s.headers || {}).length ? JSON.stringify(s.headers, null, 2) : "";
      if (s.type === "assistant" && s.params_text === undefined) s.params_text = JSON.stringify(s.params || {}, null, 2);
      if (s.type === "macro" && !s.name && s.label) s.name = s.label;
      for (const key of CONTAINERS[s.type] || []) if (Array.isArray(s[key])) s[key] = load(s[key]);
      return s;
    });
  }
  function parseJsonField(text, ru, eng) {
    const raw = String(text ?? "").trim();
    if (!raw) return {};
    try {
      const value = JSON.parse(raw);
      if (value && typeof value === "object" && !Array.isArray(value)) return value;
    } catch (e) { /* fall through */ }
    throw new Error(T(ru, eng));
  }
  /** Editor steps → server steps; throws Error with a user-facing text on bad JSON. */
  function finalize(steps) {
    return (steps || []).map((raw) => {
      const s = { ...raw };
      delete s._c;
      if (s.type === "run") {
        const text = String(s.argv_text ?? (s.argv || []).join(" "));
        const argv = [];
        text.replace(/"([^"]*)"|'([^']*)'|(\S+)/g, (_m, q1, q2, word) => { argv.push(q1 ?? q2 ?? word); return ""; });
        s.argv = argv; delete s.argv_text;
        if (!s.name) delete s.name;
        if (!s.cwd) delete s.cwd;
      }
      if (s.type === "integration") { s.args = parseJsonField(s.args_text ?? JSON.stringify(s.args || {}), "Аргументы интеграции — некорректный JSON", "The integration arguments are invalid JSON"); delete s.args_text; if (!s.name) delete s.name; }
      if (s.type === "http") { s.headers = parseJsonField(s.headers_text ?? JSON.stringify(s.headers || {}), "Заголовки HTTP — некорректный JSON", "The HTTP headers are invalid JSON"); delete s.headers_text; }
      if (s.type === "assistant") { s.params = parseJsonField(s.params_text ?? JSON.stringify(s.params || {}), "Параметры действия — некорректный JSON", "The action parameters are invalid JSON"); delete s.params_text; }
      if (s.type === "list" && !s.into) delete s.into;
      if (s.type === "calc" && (s.decimals === "" || s.decimals === null || s.decimals === undefined)) delete s.decimals;
      if (s.type === "repeat" && s.while && !String(s.while.left || "").trim()) delete s.while;
      if (!s.label) delete s.label;
      for (const key of CONTAINERS[s.type] || []) if (Array.isArray(s[key])) s[key] = finalize(s[key]);
      return s;
    });
  }
  function walk(steps, fn, depth = 0) { (steps || []).forEach((s) => { fn(s, depth); for (const key of CONTAINERS[s.type] || []) walk(s[key], fn, depth + 1); }); }
  function count(steps) { let n = 0; walk(steps, () => { n += 1; }); return n; }
  function destructive(steps) { let yes = false; walk(steps, (s) => { if (!s.disabled && DESTRUCTIVE.includes(s.type)) yes = true; }); return yes; }
  function varNames(draft) {
    const out = new Set(Object.keys(draft.scenario_variables || {}));
    walk(draft.scenario || [], (s) => { ["name", "into", "as"].forEach((k) => { if (s[k] && /^[A-Za-z_][A-Za-z0-9_]*$/.test(String(s[k])) && !(s.type === "macro" && k === "name")) out.add(String(s[k])); }); });
    return [...out].slice(0, 40);
  }
  function brief(s) {
    const cut = (v, n = 48) => { const t = String(v ?? "").replace(/\s+/g, " ").trim(); return t.length > n ? `${t.slice(0, n - 1)}…` : t; };
    switch (s.type) {
      case "set": return ["inc", "dec", "add", "mul"].includes(s.op) ? `${s.name} ${({ inc: "+", dec: "−", add: "+", mul: "×" })[s.op]}${s.by ?? 1}` : s.op === "delete" ? `− ${s.name}` : s.op === "toggle" ? `${s.name} ⇄` : `${s.name} = ${cut(s.value, 32)}`;
      case "calc": return `${s.name} = ${cut(s.expr, 40)}`;
      case "text": case "json": case "list": case "random": case "date": return `${s.name || ""} ← ${s.op || s.mode || ""}`;
      case "file_create": case "file_edit": case "file_read": case "file_info": case "file_delete": case "folder_create": case "csv_append": return cut(s.path);
      case "file_list": return `${cut(s.path || ".", 28)}/${s.pattern || "*"}`;
      case "file_copy": case "file_move": case "zip": return `${cut(s.path, 24)} → ${cut(s.to, 24)}`;
      case "if": return s.cond ? `${cut(s.cond.left, 20)} ${s.cond.op} ${cut(s.cond.right, 16)}` : "";
      case "repeat": return s.times ? `× ${s.times}` : T("пока условие", "while");
      case "foreach": return `${s.as || "item"} ∈ ${cut(s.items, 30)}`;
      case "wait": return `${s.seconds || 5} ${T("с", "s")}`;
      case "stop": return s.status === "fail" ? T("с ошибкой", "failure") : T("успешно", "success");
      case "log": case "notify": case "telegram": case "alert": case "memory": return cut(s.text);
      case "macro": return cut(s.name || s.label);
      case "macro_call": return s.macro_id || "";
      case "run": return cut(s.argv_text ?? (s.argv || []).join(" "));
      case "system": return `${s.name} ← ${s.metric}`;
      case "open_url": case "http": return `${s.type === "http" ? `${s.method || "GET"} ` : ""}${cut(s.url, 44)}`;
      case "integration": return s.tool || "";
      case "ai": return cut(s.prompt);
      case "web_search": return cut(s.query);
      case "memubase": return `${s.op || "insert"} · ${s.table || ""}`;
      case "task": return `${s.op || "run"} · ${s.task_id || ""}`;
      case "module": return `${s.module || ""} · ${s.action || ""}`;
      case "command": return s.command || "";
      case "assistant": return s.action || "";
      default: return "";
    }
  }

  /* ------------------------------------------------------------------ rendering */
  const sel = (path, value, options, extra = "") => `<select class="pinput compact" data-path="${H(path)}" ${extra}>${options.map(([v, l]) => `<option value="${H(v)}" ${String(v) === String(value ?? "") ? "selected" : ""}>${H(l)}</option>`).join("")}</select>`;
  function fieldHtml(fd, step, base, ctx) {
    if (fd.show && !fd.show(step)) return "";
    const path = `${base}.${fd.k}`, value = step[fd.k];
    const custom = fd.labelFor ? fd.labelFor(step) : null;
    const label = custom && custom[0] ? T(custom[0], custom[1] || custom[0]) : fd.label();
    const req = fd.req ? ' <i class="psc-req" aria-hidden="true">*</i>' : "";
    const lab = `<span class="psc-label">${H(label)}${req}</span>`;
    const ph = fd.ph ? ` placeholder="${H(fd.ph)}"` : "";
    const rr = fd.rerender ? ' data-rerender="1"' : "";
    switch (fd.t) {
      case "bool": return `<label class="pln-check psc-bool"><input type="checkbox" data-path="${H(path)}" data-bool="1" ${value ? "checked" : ""}><span>${H(label)}</span></label>`;
      case "num": return `<label class="psc-field short">${lab}<input class="pinput compact num" type="number" data-path="${H(path)}" data-num="1" ${fd.min !== undefined ? `min="${fd.min}"` : ""} ${fd.max !== undefined ? `max="${fd.max}"` : ""} value="${H(value ?? "")}"${ph}></label>`;
      case "area": case "code": return `<label class="psc-field wide">${lab}<textarea class="ptexta ${fd.t === "code" ? "psc-code" : ""}" rows="${fd.rows || (fd.t === "code" ? 2 : 2)}" data-path="${H(path)}" spellcheck="${fd.t === "code" ? "false" : "true"}"${ph}>${H(value ?? "")}</textarea></label>`;
      case "sel": return `<label class="psc-field">${lab}${sel(path, value ?? fd.def, fd.opts(), rr)}</label>`;
      case "var": return `<label class="psc-field short">${lab}<input class="pinput compact psc-var" data-path="${H(path)}" value="${H(value ?? "")}" maxlength="41" spellcheck="false" autocomplete="off"${ph}></label>`;
      case "cond": {
        if (fd.optional && !value) return `<button type="button" class="pln-add-sm psc-cond-add" data-act="scen-cond-on" data-path="${H(path)}">${I("plus")}${H(label)}</button>`;
        const c = value || {};
        return `<div class="psc-field wide psc-cond">${lab}<div class="pln-line"><input class="pinput compact" data-path="${H(path)}.left" value="${H(c.left || "")}" placeholder="\${count}" spellcheck="false">
          ${sel(`${path}.op`, c.op || "eq", COND_OPS(), 'data-rerender="1"')}
          ${NO_RIGHT.includes(c.op) ? "" : `<input class="pinput compact" data-path="${H(path)}.right" value="${H(c.right ?? "")}" placeholder="${T("значение", "value")}" spellcheck="false">`}
          ${fd.optional ? `<button type="button" class="pln-x-sm" data-act="scen-cond-off" data-path="${H(path)}" aria-label="${T("Убрать условие", "Remove the condition")}">${I("close")}</button>` : ""}</div></div>`;
      }
      case "model": {
        const models = ctx.models ? ctx.models() : [];
        return `<label class="psc-field">${lab}${sel(path, value || "", [["", T("— модель задачи / по умолчанию —", "— task / default model —")], ...models.map((m) => [m.id, m.name || m.id])])}</label>`;
      }
      case "tool": {
        const tools = ctx.meta()?.integrations || [];
        const title = (t) => { const v = t.title; const name = v && typeof v === "object" ? (en() ? v.en : v.ru) || v.en || v.ru : typeof v === "string" ? v : ""; return name ? `${t.group ? `${t.group}: ` : ""}${name}` : t.name; };
        const choices = [["", T("— выберите —", "— choose —")], ...tools.map((t) => [t.name, `${title(t)}${t.connected ? "" : T(" (не подключено)", " (disconnected)")}`])];
        if (value && !tools.some((t) => t.name === value)) choices.push([value, value]);
        return `<label class="psc-field wide">${lab}${sel(path, value || "", choices)}</label>`;
      }
      case "macro": {
        const macros = ctx.meta()?.macros || [];
        const choices = [["", macros.length ? T("— выберите —", "— choose —") : T("— библиотека пуста —", "— the library is empty —")], ...macros.map((m) => [m.id, `${m.name}${m.steps ? ` · ${m.steps}` : ""}`])];
        if (value && !macros.some((m) => m.id === value)) choices.push([value, value]);
        return `<label class="psc-field wide">${lab}${sel(path, value || "", choices)}</label>`;
      }
      case "task": {
        const tasks = (ctx.tasks ? ctx.tasks() : []).filter((t) => t.id !== ctx.draft().id);
        const choices = [["", T("— выберите задачу —", "— choose a task —")], ...tasks.map((t) => [t.id, t.name || t.id])];
        if (value && !tasks.some((t) => t.id === value)) choices.push([value, value]);
        return `<label class="psc-field wide">${lab}${sel(path, value || "", choices)}</label>`;
      }
      case "command": {
        const cmds = ctx.meta()?.commands || [];
        const choices = [["", T("— выберите —", "— choose —")], ...cmds.map((c) => [c.name, c.name])];
        if (value && !cmds.some((c) => c.name === value)) choices.push([value, value]);
        return `<label class="psc-field wide">${lab}${sel(path, value || "", choices)}</label>`;
      }
      case "module": {
        const rows = ctx.meta()?.module_actions || [];
        return `<label class="psc-field">${lab}${sel(path, value || "", [["", T("— модуль —", "— module —")], ...rows.map((r) => [r.module, en() ? r.title_en : r.title])], rr)}</label>`;
      }
      case "maction": {
        const mod = step.module || "";
        const row = (ctx.meta()?.module_actions || []).find((r) => r.module === mod);
        const declared = (row?.actions || []).filter((a) => a.action?.kind === "module").map((a) => [a.id, en() ? a.title_en : a.title]);
        const handlers = (ctx.meta()?.module_handlers || []).filter((h) => h.module === mod).map((h) => [h.handler, `${h.title}${h.ai ? " · AI" : ""}`]);
        const choices = [["", mod ? T("— действие —", "— action —") : T("сначала модуль", "pick a module first")], ...declared, ...handlers.filter(([id]) => !declared.some(([d]) => d === id))];
        if (value && !choices.some(([v]) => v === value)) choices.push([value, value]);
        return `<label class="psc-field wide">${lab}${sel(path, value || "", choices)}</label>`;
      }
      default: return `<label class="psc-field ${fd.wide ? "wide" : ""}">${lab}<input class="pinput compact" data-path="${H(path)}" value="${H(value ?? "")}"${ph} spellcheck="false"></label>`;
    }
  }
  function containerTitle(type, key) {
    if (type === "if") return key === "then" ? T("Тогда", "Then") : T("Иначе", "Else");
    if (type === "try") return key === "steps" ? T("Попробовать", "Try") : T("Если ошибка", "On error");
    return T("Шаги", "Steps");
  }
  function stepHtml(step, path, depth, idx, total, ctx) {
    const spec = SCHEMA()[step.type] || { icon: "info", fields: [], desc: "" };
    const collapsed = !!step._c;
    const danger = DESTRUCTIVE.includes(step.type);
    const b = brief(step);
    const head = `<div class="psc-head">
        <span class="psc-num">${idx + 1}</span><span class="psc-ico c-${H(spec.cat || "data")}">${I(spec.icon)}</span>
        <button type="button" class="psc-title" data-act="scen-fold" data-path="${H(path)}" aria-expanded="${!collapsed}"><b>${H(step.label || title(step.type))}</b>${b ? `<small>${H(b)}</small>` : ""}</button>
        ${danger ? `<span class="psc-tag warn" title="${T("Пишет файлы или запускает программы", "Writes files or runs programs")}">${I("shield")}</span>` : ""}
        ${step.disabled ? `<span class="psc-tag">${T("выключен", "off")}</span>` : ""}${step.continue_on_error ? `<span class="psc-tag">${T("ошибка не прерывает", "errors continue")}</span>` : ""}
        <span class="psc-tools">
          <button type="button" class="pln-x-sm" data-act="scen-up" data-path="${H(path)}" ${idx === 0 ? "disabled" : ""} title="${T("Выше", "Up")}" aria-label="${T("Выше", "Up")}">${I("arrowUp")}</button>
          <button type="button" class="pln-x-sm" data-act="scen-down" data-path="${H(path)}" ${idx === total - 1 ? "disabled" : ""} title="${T("Ниже", "Down")}" aria-label="${T("Ниже", "Down")}">${I("arrowDown")}</button>
          <button type="button" class="pln-x-sm" data-act="scen-dup" data-path="${H(path)}" title="${T("Дублировать", "Duplicate")}" aria-label="${T("Дублировать", "Duplicate")}">${I("copy")}</button>
          <button type="button" class="pln-x-sm" data-act="scen-off" data-path="${H(path)}" title="${step.disabled ? T("Включить шаг", "Enable the step") : T("Выключить шаг", "Disable the step")}" aria-label="${T("Вкл/выкл", "On/off")}">${I(step.disabled ? "eye" : "eyeOff")}</button>
          <button type="button" class="pln-x-sm" data-act="scen-del" data-path="${H(path)}" title="${T("Удалить шаг", "Delete the step")}" aria-label="${T("Удалить шаг", "Delete the step")}">${I("trash")}</button>
        </span></div>`;
    if (collapsed) return `<div class="psc-step is-folded ${step.disabled ? "is-off" : ""}" data-step="${H(path)}">${head}</div>`;
    const fields = spec.fields.map((fd) => fieldHtml(fd, step, path, ctx)).join("");
    const nested = (CONTAINERS[step.type] || []).map((key) => {
      const list = Array.isArray(step[key]) ? step[key] : [];
      return `<div class="psc-sub"><div class="psc-sub-head">${I(step.type === "if" && key === "else" ? "split" : "forward")}<b>${H(containerTitle(step.type, key))}</b><span>${list.length}</span></div>
        <div class="psc-list">${list.map((s, i) => stepHtml(s, `${path}.${key}.${i}`, depth + 1, i, list.length, ctx)).join("") || `<p class="psc-empty">${T("Пусто", "Empty")}</p>`}</div>
        ${depth + 1 < MAX_DEPTH ? `<button type="button" class="pln-add-sm" data-act="scen-add" data-path="${H(`${path}.${key}`)}">${I("plus")}${T("шаг", "step")}</button>` : ""}</div>`;
    }).join("");
    const extra = `<details class="psc-more"><summary>${T("Ещё", "More")}</summary>
        <label class="psc-field wide"><span class="psc-label">${T("Своё название шага", "Custom step name")}</span><input class="pinput compact" data-path="${H(path)}.label" value="${H(step.label || "")}" maxlength="120" placeholder="${H(title(step.type))}"></label>
        <label class="pln-check psc-bool"><input type="checkbox" data-path="${H(path)}.continue_on_error" data-bool="1" ${step.continue_on_error ? "checked" : ""}><span>${T("Ошибка шага не прерывает сценарий", "A step error does not stop the scenario")}</span></label>
        <p class="pln-explain">${H(spec.desc)}</p></details>`;
    return `<div class="psc-step ${step.disabled ? "is-off" : ""} ${danger ? "is-danger" : ""}" data-step="${H(path)}">${head}<div class="psc-body"><div class="psc-fields">${fields}</div>${nested}${extra}</div></div>`;
  }
  function varsBar(draft) {
    const builtins = ["today", "now", "time", "weekday", "timestamp", "task.name", "last.text", "last.ok", "error", "loop.index", "item"];
    const own = varNames(draft);
    const chip = (name, cls = "") => `<button type="button" class="psc-chip ${cls}" data-act="scen-var" data-var="${H(name)}" title="${T("Вставить в поле", "Insert into the field")}">\${${H(name)}}</button>`;
    return `<div class="psc-vars"><span>${I("hash")}${T("Переменные", "Variables")}:</span>${own.map((n) => chip(n, "own")).join("")}${builtins.map((n) => chip(n)).join("")}<button type="button" class="psc-chip" data-act="scen-var" data-var="global.NAME">\${global.…}</button></div>`;
  }
  function testHtml() {
    if (!lastTest) return "";
    if (lastTest.busy) return `<div class="psc-test"><div class="pln-nl-wait"><span class="pln-spin"></span>${T("Пробный прогон…", "Dry run…")}</div></div>`;
    if (lastTest.error) return `<div class="psc-test bad">${I("info")}<span>${H(lastTest.error)}</span></div>`;
    const r = lastTest.result || {};
    const log = (r.log || []).slice(0, 120).map((e) => `<li class="${e.ok ? "ok" : "err"} ${e.skipped ? "skip" : ""}"><span>${I(e.ok ? "check" : "close")}</span><b>${H(e.label || title(e.type))}</b><small>${H(e.detail || "")}${e.error ? ` — ${H(e.error)}` : ""}</small><em>${H(e.ms ?? "")} ms</em></li>`).join("");
    const vars = Object.entries(r.variables || {}).filter(([k]) => !["last", "global", "task"].includes(k)).slice(0, 30)
      .map(([k, v]) => `<tr><td>\${${H(k)}}</td><td>${H(typeof v === "string" ? v : JSON.stringify(v)).slice(0, 300)}</td></tr>`).join("");
    return `<div class="psc-test ${r.ok ? "ok" : "bad"}"><div class="psc-test-head">${I(r.ok ? "check" : "info")}<b>${r.ok ? T("Пробный прогон прошёл", "The dry run passed") : T("Пробный прогон: есть ошибки", "Dry run: there are errors")}</b>
        <span>${T("шагов выполнено", "steps run")}: ${H(r.steps ?? (r.log || []).length)}</span><button type="button" class="pln-x-sm" data-act="scen-test-close" aria-label="${T("Закрыть", "Close")}">${I("close")}</button></div>
      ${r.text ? `<pre class="psc-test-text">${H(String(r.text).slice(0, 3000))}</pre>` : ""}
      ${log ? `<ol class="psc-log">${log}</ol>` : ""}
      ${vars ? `<details class="psc-test-vars"><summary>${T("Переменные после прогона", "Variables after the run")}</summary><table>${vars}</table></details>` : ""}
      <p class="pln-explain">${T("В пробном прогоне файлы не пишутся, программы не запускаются, модель и сообщения не вызываются — эти шаги только описываются.", "A dry run writes no files, runs no programs and calls no model or messages — those steps are only described.")}</p></div>`;
  }
  function html(ctx) {
    const d = ctx.draft();
    const steps = d.scenario || [];
    const n = count(steps), lim = ctx.meta()?.scenario?.max_steps || 150;
    const hasDestructive = destructive(steps);
    if (lastTest && lastTest.draft !== d.id) lastTest = null;
    return `<div class="psc" id="psc">
      <div class="psc-bar"><span class="psc-count">${I("list")}${T("шагов", "steps")}: <b>${n}</b> / ${lim}</span>
        <span class="psc-bar-actions">
          <button type="button" class="pbtn ghost compact" data-act="scen-tpl">${I("sparkle")}<span>${T("Шаблон", "Template")}</span></button>
          <button type="button" class="pbtn ghost compact" data-act="scen-fold-all">${I("updown")}<span>${T("Свернуть / развернуть", "Fold / unfold")}</span></button>
          <button type="button" class="pbtn ghost compact" data-act="scen-macro-save" ${steps.length ? "" : "disabled"}>${I("archive")}<span>${T("В макросы", "To macros")}</span></button>
          <button type="button" class="pbtn compact" data-act="scen-test" ${steps.length ? "" : "disabled"}>${I("play")}<span>${T("Пробный прогон", "Dry run")}</span></button></span></div>
      <p class="pln-explain">${T("Шаги выполняются по порядку без модели (кроме шага «Спросить модель»). Результат шага доступен как ${last.text}, ошибка — ${error}; переменные подставляются как ${имя}.", "Steps run in order without a model (except “Ask a model”). A step result is ${last.text}, an error is ${error}; variables are substituted as ${name}.")}</p>
      ${varsBar(d)}
      <div class="psc-list psc-root">${steps.map((s, i) => stepHtml(s, `scenario.${i}`, 0, i, steps.length, ctx)).join("") || `<div class="psc-empty big">${I("terminal")}<b>${T("Добавьте первый шаг", "Add the first step")}</b><span>${T("или начните с шаблона", "or start from a template")}</span></div>`}</div>
      <button type="button" class="pln-add" data-act="scen-add" data-path="scenario">${I("plus")}${T("Добавить шаг", "Add a step")}</button>
      <div id="psc-test-box">${testHtml()}</div>
      <details class="pln-adv psc-settings" ${d.scenario_base || (d.run_allowlist_text || "").trim() || Object.keys(d.scenario_variables || {}).length ? "open" : ""}><summary>${I("settings")}${T("Папка, программы и начальные переменные", "Folder, programs and initial variables")}</summary>
        <div class="pln-line"><span>${T("Папка для файлов", "Folder for files")}</span><input class="pinput compact wide" data-path="scenario_base" value="${H(d.scenario_base || "")}" placeholder="${H(ctx.meta()?.data_dir || T("каталог данных MemuAI", "MemuAI data folder"))}"><button type="button" class="pbtn ghost compact" data-act="pick-folder" data-target="scenario_base">${I("folder")}${T("Выбрать…", "Choose…")}</button></div>
        <p class="pln-explain">${T("Файлы создаются и правятся только внутри этой папки (по умолчанию — каталог данных). Выход через .. и симлинки запрещён.", "Files are created and edited only inside this folder (the data folder by default). Escaping via .. or symlinks is forbidden.")}</p>
        <div class="pln-line"><span>${T("Белый список программ", "Program allowlist")}</span><input class="pinput compact wide" data-path="run_allowlist_text" value="${H(d.run_allowlist_text ?? (d.run_allowlist || []).join(", "))}" placeholder="notepad.exe, calc.exe"></div>
        <p class="pln-explain">${T("Пустой список — ничего запускать нельзя. Имена программ через запятую; запуск — только argv-массивом, без shell, с таймаутом.", "An empty list means nothing may run. Program names separated by commas; runs use an argv array, no shell, with a timeout.")}</p>
        <label class="field-label">${T("Начальные переменные (JSON)", "Initial variables (JSON)")}</label><textarea class="ptexta psc-code" rows="2" data-path="scenario_variables_text" spellcheck="false">${H(d.scenario_variables_text ?? JSON.stringify(d.scenario_variables || {}, null, 2))}</textarea></details>
      ${hasDestructive ? `<div class="pln-note warn">${I("shield")}<span>${T("Сценарий пишет файлы или запускает программы.", "The scenario writes files or runs programs.")}</span>
        <label class="pln-check"><input type="checkbox" data-path="confirmed" data-bool="1" ${d.confirmed ? "checked" : ""}><span>${T("Я понимаю и подтверждаю: при запуске сценарий будет писать файлы и/или запускать программы из белого списка.", "I understand and confirm: when run, the scenario will write files and/or run allowlisted programs.")}</span></label></div>` : ""}
    </div>`;
  }

  /* ------------------------------------------------------------------ templates */
  function templates() {
    const md = T("# Дневник ${today}\n\n## Главное\n- \n\n## Сделано\n- \n", "# Journal ${today}\n\n## Highlights\n- \n\n## Done\n- \n");
    return [
      { id: "journal", icon: "book", title: T("Дневник на каждый день", "A daily journal"), desc: T("Файл journal/ДАТА.md по шаблону, если его ещё нет", "journal/DATE.md from a template unless it exists"), confirmed: true,
        steps: [{ type: "file_info", path: "journal/${today}.md", name: "file" }, { type: "if", cond: { left: "${file.exists}", op: "is_true", right: "" },
          then: [{ type: "log", text: T("Дневник уже есть: journal/${today}.md", "The journal exists: journal/${today}.md") }],
          else: [{ type: "file_create", path: "journal/${today}.md", content: md, overwrite: false }, { type: "log", text: T("Создан journal/${today}.md", "Created journal/${today}.md") }] }] },
      { id: "backup", icon: "archive", title: T("ZIP-копия папки заметок", "A ZIP copy of the notes folder"), desc: T("notes → backups/notes-ДАТА.zip и строка в журнале", "notes → backups/notes-DATE.zip and a log line"), confirmed: true,
        steps: [{ type: "folder_create", path: "notes" }, { type: "zip", path: "notes", to: "backups/notes-${today}.zip", overwrite: true }, { type: "csv_append", path: "backups/log.csv", values: "${now}|notes-${today}.zip", header: "time|archive", delimiter: ";" }, { type: "log", text: T("Копия: backups/notes-${today}.zip", "Backup: backups/notes-${today}.zip") }] },
      { id: "disk", icon: "monitor", title: T("Мало места на диске → уведомление", "Low disk space → a notification"), desc: T("Проверить свободное место и предупредить ниже 15%", "Check free space and warn below 15%"),
        steps: [{ type: "system", metric: "disk_free_pct", name: "free" }, { type: "if", cond: { left: "${free}", op: "lt", right: "15" },
          then: [{ type: "alert", title: T("Мало места на диске", "Low disk space"), text: T("Свободно ${free}% — пора почистить диск.", "Only ${free}% free — time to clean up."), windows: true, inbox: true }],
          else: [{ type: "log", text: T("Места достаточно: ${free}%", "Enough space: ${free}%") }] }] },
      { id: "counter", icon: "hash", title: T("Счётчик запусков с отметкой", "A run counter with a milestone"), desc: T("Переменная задачи +1, каждые 10 — сообщение", "A task variable +1, a message every 10"),
        steps: [{ type: "set", name: "runs", op: "inc", by: 1, scope: "task" }, { type: "calc", name: "rest", expr: "runs % 10" },
          { type: "if", cond: { left: "${rest}", op: "eq", right: "0" }, then: [{ type: "notify", text: T("Задача выполнена уже ${runs} раз", "The task has run ${runs} times") }], else: [] }] },
      { id: "http", icon: "globe", title: T("Данные API → CSV-таблица", "API data → a CSV table"), desc: T("GET-запрос, поле JSON и строка в data/stats.csv", "A GET request, a JSON field and a row in data/stats.csv"), confirmed: true,
        steps: [{ type: "http", method: "GET", url: "https://api.github.com/repos/python/cpython", parse: "json", timeout: 20, name: "response", headers_text: "" },
          { type: "json", op: "get", input: "${response}", path: "stargazers_count", name: "stars" },
          { type: "csv_append", path: "data/stats.csv", values: "${today}|${stars}", header: "date|stars", delimiter: ";" }, { type: "log", text: "stars = ${stars}" }] },
      { id: "summary", icon: "sparkle", title: T("ИИ-конспект файла", "An AI summary of a file"), desc: T("Прочитать notes/inbox.md, кратко пересказать, сохранить в summaries/", "Read notes/inbox.md, summarize, save to summaries/"), confirmed: true,
        steps: [{ type: "file_read", path: "notes/inbox.md", name: "content", as: "text", max_chars: 60000, missing_ok: true },
          { type: "if", cond: { left: "${content}", op: "not_empty", right: "" }, then: [
            { type: "ai", prompt: T("Сделай конспект на 5–7 пунктов и список дел:\n\n${content}", "Write a 5–7 bullet summary and a to-do list:\n\n${content}"), name: "answer" },
            { type: "file_create", path: "summaries/${today}.md", content: "${answer}", overwrite: true }], else: [{ type: "log", text: T("notes/inbox.md пуст", "notes/inbox.md is empty") }] }] },
      { id: "files", icon: "folder", title: T("Отчёт о новых файлах", "A report of recent files"), desc: T("10 последних файлов папки → список в отчёте", "The 10 newest files of a folder → a report list"),
        steps: [{ type: "file_list", path: ".", pattern: "*", recursive: false, sort: "modified", limit: 10, name: "files" },
          { type: "foreach", items: "${files}", as: "item", limit: 10, steps: [{ type: "log", text: "• ${item.name} (${item.size} B)" }] }] },
      { id: "quote", icon: "shuffle", title: T("Случайная мысль дня", "A random thought of the day"), desc: T("Выбор из списка и сообщение", "A pick from a list and a message"),
        steps: [{ type: "random", mode: "pick", items: T("Сделай перерыв\nВыпей воды\nРазомнись 5 минут\nЗапиши 3 благодарности", "Take a break\nDrink water\nStretch for 5 minutes\nWrite down 3 things you are grateful for"), name: "tip" },
          { type: "notify", text: "${tip}" }] },
    ];
  }

  /* ------------------------------------------------------------------ actions */
  function arrAndIndex(draft, path) {
    const parts = String(path).split(".");
    const idx = +parts.pop();
    return [getPath(draft, parts.join(".")), idx];
  }
  function deepCopy(v) { return JSON.parse(JSON.stringify(v)); }
  function addMenu(anchor, path, ctx) {
    const arr = getPath(ctx.draft(), path);
    if (!Array.isArray(arr)) return;
    const schema = SCHEMA();
    const depth = String(path).split(".").filter((x) => ["then", "else", "steps", "catch"].includes(x)).length;
    const items = [];
    for (const [cat, , catTitle] of CATS()) {
      items.push({ sep: catTitle });
      for (const [type, spec] of Object.entries(schema)) {
        if (spec.cat !== cat) continue;
        if (CONTAINERS[type] && depth >= MAX_DEPTH - 1) continue;
        items.push({ icon: spec.icon, title: title(type), desc: spec.desc, badge: spec.destructive ? T("запись", "writes") : "", run: () => {
          const step = fresh(type);
          if (type === "module") {
            const first = (ctx.meta()?.module_actions || [])[0];
            if (first) step.module = first.module;
          }
          arr.push(step); ctx.rerender();
        } });
      }
    }
    ctx.popMenu(anchor, items);
  }
  function tplMenu(anchor, ctx) {
    ctx.popMenu(anchor, templates().map((tpl) => ({ icon: tpl.icon, title: tpl.title, desc: tpl.desc, run: () => {
      const d = ctx.draft();
      d.scenario = [...(d.scenario || []), ...load(deepCopy(tpl.steps))];
      if (!d.name) d.name = tpl.title;
      ctx.rerender();
    } })));
  }
  function insertVar(name, root) {
    const token = `\${${name}}`;
    const el = lastFocus && root.contains(lastFocus) ? lastFocus : null;
    if (el && !el.classList.contains("psc-var")) {
      const start = el.selectionStart ?? el.value.length, end = el.selectionEnd ?? el.value.length;
      el.value = el.value.slice(0, start) + token + el.value.slice(end);
      el.focus();
      try { el.setSelectionRange(start + token.length, start + token.length); } catch (e) { /* number inputs */ }
      el.dispatchEvent(new Event("input", { bubbles: true }));
      return true;
    }
    try { navigator.clipboard.writeText(token); } catch (e) { /* clipboard is optional */ }
    if (typeof toast === "function") toast(T(`Скопировано: ${token}`, `Copied: ${token}`));
    return false;
  }
  async function dryRun(ctx) {
    const d = ctx.draft();
    let body;
    try {
      const server = ctx.serverDraft();
      body = { scenario: server.scenario || [], scenario_base: server.scenario_base || "", run_allowlist: server.run_allowlist || [],
        scenario_variables: server.scenario_variables || {}, task_id: ctx.editing() ? d.id : "", name: d.name || "" };
    } catch (e) { lastTest = { draft: d.id, error: e.message || String(e) }; paintTest(); return; }
    lastTest = { draft: d.id, busy: true }; paintTest();
    try { lastTest = { draft: d.id, result: await ctx.call("/api/plan/scenario/test", body) }; }
    catch (e) { lastTest = { draft: d.id, error: e.message || String(e) }; }
    paintTest();
  }
  function paintTest() { const box = $q("#psc-test-box"); if (box) { box.innerHTML = testHtml(); box.scrollIntoView?.({ block: "nearest", behavior: document.documentElement.dataset.motion === "off" ? "auto" : "smooth" }); } }
  /** A small dialog inside the panel: panel() itself would replace the open builder. */
  function inlineAsk(heading, label, value) {
    return new Promise((resolve) => {
      const host = $q("#panel") || document.body;
      const ov = document.createElement("div");
      ov.className = "pln-picker psc-ask";
      ov.innerHTML = `<form class="pln-picker-box" role="dialog" aria-label="${H(heading)}"><div class="pln-picker-head"><b>${H(heading)}</b><button type="button" class="mini-action" data-x aria-label="${T("Закрыть", "Close")}">${I("close")}</button></div>
        <label class="field-label">${H(label)}</label><input class="pinput" data-q maxlength="60" value="${H(value || "")}">
        <div class="row-end"><button type="button" class="pbtn ghost" data-x>${T("Отмена", "Cancel")}</button><button class="pbtn" type="submit">${I("check")}${T("Сохранить", "Save")}</button></div></form>`;
      host.appendChild(ov);
      const input = $q("[data-q]", ov);
      const done = (v) => { ov.remove(); resolve(v); };
      ov.addEventListener("click", (e) => { if (e.target === ov || e.target.closest("[data-x]")) done(""); });
      ov.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); done(""); } });
      $q("form", ov).addEventListener("submit", (e) => { e.preventDefault(); done(input.value.trim()); });
      input.focus(); input.select();
    });
  }
  async function saveMacro(ctx) {
    const d = ctx.draft();
    let steps;
    try { steps = finalize(deepCopy(d.scenario || [])); } catch (e) { if (typeof toast === "function") toast(e.message, "err"); return; }
    const name = await inlineAsk(T("Сохранить шаги как макрос", "Save the steps as a macro"), T("Название макроса", "Macro name"), d.name || T("Мой макрос", "My macro"));
    if (!name || !String(name).trim()) return;
    try {
      const res = await ctx.call("/api/plan/macros", { op: "save", macro: { name: String(name).trim().slice(0, 60), description: d.summary || "", steps } });
      const meta = ctx.meta();
      if (meta) meta.macros = (res.macros || []).map((m) => ({ id: m.id, name: m.name, description: m.description || "", steps: (m.steps || []).length, destructive: !!m.destructive }));
      if (typeof toast === "function") toast(T("Макрос сохранён — вызывайте его шагом «Макрос»", "Macro saved — call it with the “Macro” step"));
    } catch (e) { if (typeof toast === "function") toast(e.message || String(e), "err"); }
  }
  function click(el, ctx) {
    const act = el.dataset.act, path = el.dataset.path, d = ctx.draft();
    if (act === "scen-add") { addMenu(el, path, ctx); return true; }
    if (act === "scen-tpl") { tplMenu(el, ctx); return true; }
    if (act === "scen-test") { dryRun(ctx); return true; }
    if (act === "scen-test-close") { lastTest = null; paintTest(); return true; }
    if (act === "scen-macro-save") { saveMacro(ctx); return true; }
    if (act === "scen-var") { insertVar(el.dataset.var, el.closest("#psc") || document); return true; }
    if (act === "scen-fold-all") {
      const steps = d.scenario || [];
      const fold = steps.some((s) => !s._c);
      steps.forEach((s) => { s._c = fold; });
      ctx.rerender(false); return true;
    }
    if (act === "scen-cond-on") { const host = getPath(d, path.replace(/\.[^.]+$/, "")); if (host) host[path.split(".").pop()] = { left: "", op: "eq", right: "" }; ctx.rerender(); return true; }
    if (act === "scen-cond-off") { const host = getPath(d, path.replace(/\.[^.]+$/, "")); if (host) delete host[path.split(".").pop()]; ctx.rerender(); return true; }
    const [arr, idx] = arrAndIndex(d, path || "");
    if (!Array.isArray(arr) || !arr[idx]) return false;
    if (act === "scen-del") arr.splice(idx, 1);
    else if (act === "scen-up" && idx > 0) [arr[idx - 1], arr[idx]] = [arr[idx], arr[idx - 1]];
    else if (act === "scen-down" && idx < arr.length - 1) [arr[idx + 1], arr[idx]] = [arr[idx], arr[idx + 1]];
    else if (act === "scen-dup") arr.splice(idx + 1, 0, deepCopy(arr[idx]));
    else if (act === "scen-off") { if (arr[idx].disabled) delete arr[idx].disabled; else arr[idx].disabled = true; }
    else if (act === "scen-fold") { arr[idx]._c = !arr[idx]._c; ctx.rerender(false); return true; }
    else return false;
    ctx.rerender();
    return true;
  }
  function bind(root) {
    root.addEventListener("focusin", (e) => {
      const t = e.target;
      if (t.closest && t.closest("#psc") && t.matches("textarea, input:not([type=checkbox]):not([type=number])")) lastFocus = t;
    });
  }

  window.MemuPlanScen = { inlineAsk, html, bind, click, fresh, load, finalize, templates, count, destructive, title, schema: SCHEMA, categories: CATS };
})();
