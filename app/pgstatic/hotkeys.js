

(function (root) {
  "use strict";

  const GROUPS = [
    { id: "main", icon: "keyboard", ru: "Главное окно", en: "Main window",
      desc_ru: "Работают везде в окне MemuAI, кроме открытых диалогов.", desc_en: "Work anywhere in the MemuAI window except open dialogs." },
    { id: "chat", icon: "chat", ru: "Чат и поле ввода", en: "Chat and composer",
      desc_ru: "Когда курсор в поле сообщения или в списках чата.", desc_en: "While typing a message or in the chat lists." },
    { id: "settings", icon: "settings", ru: "Настройки и документация", en: "Settings and documentation",
      desc_ru: "Внутри окна настроек, в том числе в разделе «Документация».", desc_en: "Inside the settings window, including Documentation." },
    { id: "code", icon: "terminal", ru: "MemuCode", en: "MemuCode",
      desc_ru: "В режиме MemuCode: проводник, палитра, панель справа, терминал.", desc_en: "In MemuCode: explorer, palette, right panel, terminal." },
    { id: "editor", icon: "edit", ru: "Редактор файлов", en: "File editor",
      desc_ru: "Когда открыт редактор файла (MemuCode, Work, мастерская модулей).", desc_en: "While a file editor is open (MemuCode, Work, the module workbench)." },
    { id: "modules", icon: "work", ru: "Окна модулей", en: "Module windows",
      desc_ru: "Когда открыто окно модуля поверх MemuAI.", desc_en: "While a module window is open above MemuAI." },
    { id: "sketch", icon: "palette", ru: "Набросок", en: "Sketch",
      desc_ru: "В окне рисования «Набросок» (＋ → Набросок).", desc_en: "In the Sketch drawing window (＋ → Sketch)." },
    { id: "panels", icon: "chart", ru: "Панели", en: "Panels",
      desc_ru: "Карточки на главном экране и сбоку: задачи, заметки, меню.", desc_en: "Cards on the home screen and in the side: to-dos, notes, menus." },
    { id: "media", icon: "image", ru: "Просмотр и медиа", en: "Viewers and media",
      desc_ru: "Просмотр картинок, плеер озвучки, карточки товаров, графики.", desc_en: "Image viewer, the voice player, product cards, charts." },
    { id: "quick", icon: "zap", ru: "Окно быстрого доступа", en: "Quick Ask window",
      desc_ru: "Внутри окна Alt+Space.", desc_en: "Inside the Alt+Space window." },
    { id: "quick_tools", icon: "clipboard", ru: "Инструменты быстрого доступа", en: "Quick Ask tools",
      desc_ru: "Область экрана, буфер, сниппеты, стикеры, выделенный текст, панели и кнопки.", desc_en: "Screen region, clipboard, snippets, stickers, selected text, panels and buttons." },
    { id: "global", icon: "globe", ru: "Глобальные (в любой программе)", en: "Global (in any program)",
      desc_ru: "Работают во всех программах Windows, пока запущен MemuAI.", desc_en: "Work in every Windows program while MemuAI runs." },
  ];

  const SCOPES = {

    main: ["main", "code", "modules", "global"],
    code: ["main", "code", "global"],
    editor: ["editor", "global"],
    modules: ["main", "modules", "global"],
    chat: ["chat", "global"],
    settings: ["settings", "global"],
    docs: ["docs", "settings", "global"],
    sketch: ["sketch", "global"],
    panels: ["panels", "global"],
    media: ["media", "global"],
    quick: ["quick", "quick_tools", "global"],
    quick_tools: ["quick", "quick_tools", "global"],
    global: ["main", "code", "editor", "modules", "chat", "settings", "docs", "sketch", "panels", "media", "quick", "quick_tools", "global"],
  };

  const E = (id, group, keys, ru, en, extra = {}) => ({ id, group, keys: Array.isArray(keys) ? keys : [keys], ru, en,
    custom: false, scope: group, ...extra });
  const C = (id, group, key, ru, en, extra = {}) => E(id, group, [key], ru, en, { custom: true, ...extra });

  const REGISTRY = [

    C("new_chat", "main", "Ctrl+N", "Новый чат", "New chat", { action: "new_chat" }),
    C("temporary", "main", "Ctrl+Shift+T", "Временный чат (или сделать текущий постоянным)", "Temporary chat (or make the current one permanent)"),
    C("search", "main", "Ctrl+K", "Поиск везде: чаты, память, файлы, настройки", "Search everywhere: chats, memory, files, settings"),
    C("composer", "main", "Ctrl+L", "Перейти к полю ввода", "Focus the composer"),
    C("models", "main", "Ctrl+M", "Открыть список моделей", "Open the model list"),
    C("sidebar", "main", "Ctrl+B", "Скрыть или показать боковую панель", "Hide or show the sidebar"),
    C("focus", "main", "Ctrl+Shift+F", "Режим фокуса", "Focus mode"),
    C("export", "main", "Ctrl+Shift+S", "Экспорт чата", "Export the chat"),
    C("settings", "main", "Ctrl+Comma", "Настройки", "Settings"),
    C("help", "main", "Ctrl+Slash", "Шпаргалка горячих клавиш", "Keyboard shortcut cheat sheet"),
    C("back", "main", "Alt+ArrowLeft", "Назад по истории", "History back"),
    C("forward", "main", "Alt+ArrowRight", "Вперёд по истории", "History forward"),
    C("mode_chat", "main", "Ctrl+1", "Раздел «Чат»", "Chat section", { added: 29 }),
    C("mode_work", "main", "Ctrl+2", "Раздел Work", "Work section", { added: 29 }),
    C("mode_code", "main", "Ctrl+3", "Раздел MemuCode", "MemuCode section", { added: 29 }),
    C("mode_schedule", "main", "Ctrl+4", "Планирование", "Scheduling", { added: 29 }),
    C("mode_skills", "main", "Ctrl+5", "Скиллы ИИ", "AI skills", { added: 29 }),
    C("chat_prev", "main", "Alt+ArrowUp", "Предыдущий чат в списке", "Previous chat in the list", { added: 29 }),
    C("chat_next", "main", "Alt+ArrowDown", "Следующий чат в списке", "Next chat in the list", { added: 29 }),
    C("rename_chat", "main", "F2", "Переименовать текущий чат", "Rename the current chat", { added: 29 }),
    C("pin_chat", "main", "", "Закрепить или открепить текущий чат", "Pin or unpin the current chat", { added: 29 }),
    C("copy_last", "main", "Ctrl+Shift+C", "Скопировать последний ответ", "Copy the last answer", { added: 29 }),
    C("attach", "main", "Ctrl+O", "Прикрепить файл", "Attach a file", { added: 29 }),
    C("dictation", "main", "Ctrl+Shift+M", "Голосовой ввод (диктовка)", "Voice input (dictation)", { added: 29 }),
    C("live", "main", "Ctrl+Shift+L", "Разговор MemuAI Live", "MemuAI Live conversation", { added: 29 }),
    C("docs", "main", "Ctrl+Shift+H", "Документация", "Documentation", { added: 29 }),
    C("zoom_in", "main", "Ctrl+Equal", "Крупнее интерфейс", "Larger interface", { added: 29 }),
    C("zoom_out", "main", "Ctrl+Minus", "Мельче интерфейс", "Smaller interface", { added: 29 }),
    C("zoom_reset", "main", "Ctrl+0", "Обычный размер интерфейса (100 %)", "Default interface size (100%)", { added: 29 }),
    C("theme", "main", "", "Сменить тему: светлая / тёмная", "Switch theme: light / dark", { added: 29 }),
    C("find_in_chat", "main", "Ctrl+F", "Найти в этом чате (Enter / F3 — дальше, ★ — закладки)", "Find in this chat (Enter / F3 — next, ★ — bookmarks)", { added: 37 }),

    E("help_f1", "main", "F1", "Шпаргалка горячих клавиш", "Keyboard shortcut cheat sheet"),
    E("escape", "main", "Escape", "Остановить ответ; закрыть меню, диалог, просмотр картинки", "Stop the answer; close a menu, dialog or image viewer"),
    E("copy_selection", "main", "Ctrl+C", "Скопировать выделенный текст сообщения", "Copy the selected message text"),
    E("reload", "main", "Ctrl+R", "Перезагрузить интерфейс", "Reload the interface"),
    E("system_menu", "main", "Alt+Space", "Меню окна Windows (когда быстрый доступ на другом сочетании)", "The Windows window menu (when Quick Ask uses another combination)"),
    E("close_app", "main", "Alt+F4", "Закрыть MemuAI", "Close MemuAI"),

    E("send", "chat", "Enter", "Отправить (или Ctrl+Enter — смотря по настройке «Отправка сообщений»)", "Send (or Ctrl+Enter, depending on the “Sending messages” setting)"),
    E("newline", "chat", "Shift+Enter", "Новая строка", "New line"),
    E("recall_last", "chat", "ArrowUp", "В пустом поле: вернуть своё последнее сообщение для правки", "In an empty composer: bring back your last message to edit", { added: 37 }),
    C("schedule_send", "chat", "Ctrl+Shift+Enter", "Отправить позже: меню отложенной отправки", "Send later: the scheduled-message menu", { added: 29 }),
    E("slash", "chat", "/", "Команды (в начале сообщения)", "Commands (at the start of a message)"),
    E("mention", "chat", "@", "Упоминания и файлы проекта", "Mentions and project files"),
    E("list_pick", "chat", ["ArrowUp", "ArrowDown", "Enter", "Tab"], "Выбор в списке команд и упоминаний", "Pick in the command and mention lists"),
    E("list_close", "chat", "Escape", "Закрыть список команд", "Close the command list"),
    E("branch_versions", "chat", ["Ctrl+Shift+ArrowLeft", "Ctrl+Shift+ArrowRight"], "Версии ответа: предыдущая / следующая (под нижним видимым сообщением с версиями)", "Answer versions: previous / next (the lowest visible message with versions)"),
    E("branch_map", "chat", "Ctrl+Shift+B", "Карта веток чата", "The chat's branch map"),
    E("paste_image", "chat", "Ctrl+V", "Вставить картинку или файл из буфера", "Paste an image or file from the clipboard"),
    E("chat_search_clear", "chat", "Escape", "В поиске по чатам: очистить поиск", "In the chat search: clear it"),
    E("model_search", "chat", ["ArrowUp", "ArrowDown", "Enter"], "Меню моделей: поиск набором текста и выбор стрелками", "Model menu: type to search, pick with the arrows"),
    E("plus_menu", "chat", ["ArrowUp", "ArrowDown", "Enter"], "Меню ＋: поиск пункта и выбор", "The ＋ menu: search an item and pick it"),
    E("dialog_enter", "chat", "Enter", "В диалогах с одним полем (переименование, память): подтвердить", "In one-field dialogs (rename, memory): confirm"),
    E("schedule_ai", "chat", "Enter", "Планирование: «опишите задачу» — создать задачу ИИ", "Scheduling: “describe the task” — create the AI task"),

    E("settings_search", "settings", ["Ctrl+F", "/"], "Поиск по настройкам", "Search settings"),
    E("settings_results", "settings", ["ArrowUp", "ArrowDown", "Enter"], "Выбор найденной настройки", "Pick a found setting"),
    E("settings_escape", "settings", "Escape", "Очистить поиск, закрыть подраздел, затем окно", "Clear the search, close a sub-page, then the window"),
    E("settings_subback", "settings", "Alt+ArrowLeft", "Назад из подраздела", "Back from a sub-page"),
    E("settings_nav", "settings", ["ArrowUp", "ArrowDown", "Home", "End"], "Перемещение по разделам слева", "Move through the pages on the left"),
    E("theme_save", "settings", "Ctrl+S", "Студия тем: сохранить тему", "Theme studio: save the theme"),
    E("theme_tab", "settings", "Tab", "Студия тем: отступ в поле CSS", "Theme studio: indent in the CSS field"),
    E("docs_search", "settings", "/", "Документация: поиск", "Documentation: search", { scope: "docs" }),
    E("docs_results", "settings", ["ArrowUp", "ArrowDown", "Enter"], "Документация: выбор результата поиска", "Documentation: pick a search result", { scope: "docs" }),
    E("docs_find", "settings", "Ctrl+F", "Документация: найти на странице", "Documentation: find on the page", { scope: "docs" }),
    E("docs_find_next", "settings", ["Enter", "Shift+Enter", "F3", "Shift+F3"], "Документация: следующее / предыдущее совпадение", "Documentation: next / previous match", { scope: "docs" }),
    E("docs_history", "settings", ["Alt+ArrowLeft", "Alt+ArrowRight"], "Документация: назад / вперёд", "Documentation: back / forward", { scope: "docs" }),

    C("code_palette", "code", "Ctrl+P", "Палитра: найти файл, команду или скилл", "Palette: find a file, command or skill"),
    C("code_commands", "code", "Ctrl+Shift+P", "Палитра сразу в режиме команд «/»", "The palette straight in “/” command mode"),
    C("code_explorer", "code", "Ctrl+Shift+E", "Показать или скрыть дерево проекта", "Show or hide the project tree"),
    C("code_dock", "code", "Ctrl+Shift+D", "Показать или скрыть правую панель", "Show or hide the right panel"),
    C("code_terminal", "code", "Ctrl+Backquote", "Перейти в терминал", "Go to the terminal"),

    E("code_tabs", "code", ["Alt+1…Alt+7"], "Разделы правой панели", "Right-panel sections"),
    E("code_escape", "code", "Escape", "В узком окне: закрыть правую панель", "In a narrow window: close the right panel"),
    E("code_tree", "code", ["ArrowUp", "ArrowDown", "Home", "End", "ArrowLeft", "ArrowRight"], "Дерево: перемещение, раскрыть и свернуть папку", "Tree: move, expand and collapse a folder"),
    E("code_tree_open", "code", ["Enter", "Space"], "Дерево: открыть файл", "Tree: open the file"),
    E("code_tree_menu", "code", ["Shift+F10", "ContextMenu"], "Дерево: меню файла", "Tree: the file menu"),
    E("code_filter", "code", ["ArrowDown", "Escape"], "Фильтр дерева: перейти к файлам / очистить", "Tree filter: go to the files / clear it"),
    E("code_view_edit", "code", "E", "Просмотр файла: начать правку (или Alt + двойной щелчок по строке)", "File viewer: start editing (or Alt + double-click a line)"),
    E("code_terminal_history", "code", ["ArrowUp", "ArrowDown"], "Терминал: история команд", "Terminal: command history"),
    E("code_palette_keys", "code", ["ArrowUp", "ArrowDown", "Enter", "Escape"], "Палитра: выбор и закрытие", "Palette: pick and close"),
    E("code_resize", "code", ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"], "Разделители панелей (в фокусе): изменить размер", "Panel splitters (focused): resize"),

    E("ed_save", "editor", "Ctrl+S", "Сохранить", "Save"),
    E("ed_undo", "editor", ["Ctrl+Z", "Ctrl+Y"], "Отменить / повторить", "Undo / redo"),
    E("ed_find", "editor", ["Ctrl+F", "Ctrl+H"], "Найти / найти и заменить", "Find / find and replace"),
    E("ed_find_next", "editor", ["F3", "Shift+F3"], "Следующее / предыдущее совпадение", "Next / previous match"),
    E("ed_find_enter", "editor", ["Enter", "Shift+Enter"], "В поле поиска: следующее / предыдущее", "In the find field: next / previous"),
    E("ed_replace_all", "editor", "Ctrl+Enter", "В поле замены: заменить все (Enter — одно)", "In the replace field: replace all (Enter — one)"),
    E("ed_goto", "editor", "Ctrl+G", "Перейти к строке", "Go to line"),
    E("ed_comment", "editor", "Ctrl+Slash", "Закомментировать строки", "Toggle comment"),
    E("ed_duplicate", "editor", "Ctrl+D", "Дублировать строку", "Duplicate line"),
    E("ed_move", "editor", ["Alt+ArrowUp", "Alt+ArrowDown"], "Переместить строку", "Move line"),
    E("ed_indent", "editor", ["Tab", "Shift+Tab"], "Отступ / убрать отступ", "Indent / outdent"),
    E("ed_escape", "editor", "Escape", "Закрыть поиск", "Close find"),

    C("mod_snap_left", "modules", "Alt+Shift+ArrowLeft", "Прижать активное окно модуля влево", "Snap the active module window left"),
    C("mod_snap_right", "modules", "Alt+Shift+ArrowRight", "Прижать окно модуля вправо", "Snap the module window right"),
    C("mod_maximize", "modules", "Alt+Shift+ArrowUp", "Развернуть окно модуля", "Maximize the module window"),
    C("mod_snap_bottom", "modules", "Alt+Shift+ArrowDown", "Прижать окно модуля вниз", "Snap the module window to the bottom"),
    C("mod_minimize", "modules", "Alt+Shift+M", "Свернуть окно модуля", "Minimize the module window"),
    C("mod_close", "modules", "Ctrl+W", "Закрыть активный модуль", "Close the active module", { added: 31 }),

    E("mod_escape", "modules", "Escape", "Развёрнутый модуль — вернуть обычное окно; окно модуля — свернуть на панель модулей", "Expanded module — back to a normal window; module window — minimize to the module bar"),
    E("mod_save", "modules", "Ctrl+S", "Мастерская модуля: сохранить код", "Module workbench: save the code"),
    E("mod_tab", "modules", "Tab", "Мастерская: отступ в редакторе кода", "Workbench: indent in the code editor"),
    E("mod_layout", "modules", "Escape", "Меню раскладки окон: закрыть", "Window layout menu: close"),

    E("sk_tools", "sketch", ["P", "M", "E", "L", "A", "R", "O", "T", "I", "V"], "Инструменты: карандаш, маркер, ластик, линия, стрелка, прямоугольник, овал, текст, картинка, перемещение", "Tools: pen, marker, eraser, line, arrow, rectangle, ellipse, text, image, move"),
    E("sk_undo", "sketch", ["Ctrl+Z", "Ctrl+Y", "Ctrl+Shift+Z"], "Отменить / повторить", "Undo / redo"),
    E("sk_attach", "sketch", "Ctrl+Enter", "Прикрепить рисунок к сообщению", "Attach the drawing to the message"),
    E("sk_delete", "sketch", ["Delete", "Backspace"], "Удалить выбранный объект (инструмент «Двигать»)", "Delete the selected object (Move tool)"),
    E("sk_text", "sketch", ["Enter", "Shift+Enter"], "Текст: поставить / новая строка", "Text: place / new line"),
    E("sk_escape", "sketch", "Escape", "Закрыть (рисунок сохранится)", "Close (the drawing is kept)"),
    E("sk_paste", "sketch", "Ctrl+V", "Вставить картинку из буфера", "Paste an image from the clipboard"),

    E("pn_menu", "panels", ["Shift+F10", "ContextMenu"], "Меню панели", "The panel menu"),
    E("pn_delete", "panels", "Delete", "Убрать панель (в фокусе)", "Remove the focused panel"),
    E("pn_todo_add", "panels", "Enter", "Задачи: добавить задачу из поля", "To-dos: add the typed task"),
    E("pn_todo_toggle", "panels", "Space", "Задачи: отметить выполненной", "To-dos: mark done"),
    E("pn_todo_edit", "panels", ["Enter", "F2"], "Задачи: открыть редактор задачи", "To-dos: open the task editor"),
    E("pn_todo_delete", "panels", ["Delete", "Backspace"], "Задачи: удалить задачу", "To-dos: delete the task"),
    E("pn_todo_move", "panels", ["ArrowUp", "ArrowDown", "Alt+ArrowUp", "Alt+ArrowDown"], "Задачи: перейти / переставить (Alt)", "To-dos: move focus / reorder (Alt)"),
    E("pn_todo_subs", "panels", ["ArrowRight", "ArrowLeft"], "Задачи: показать / скрыть подзадачи", "To-dos: show / hide subtasks"),
    E("pn_note", "panels", ["Enter", "F2", "Escape"], "Заметка: начать правку / сохранить и выйти", "Note: start editing / save and leave"),
    E("pn_ghost", "panels", ["Tab", "ArrowRight", "Escape"], "Подсказка ИИ в поле: принять / отклонить", "AI suggestion in a field: accept / dismiss"),
    E("pn_reorder", "panels", ["ArrowUp", "ArrowDown"], "Настройка панели: переставить пункт в фокусе", "Panel options: move the focused item"),

    E("md_viewer", "media", "Escape", "Закрыть просмотр картинки (колесо — масштаб, двойной щелчок — по размеру / 100 %)", "Close the image viewer (wheel — zoom, double-click — fit / 100%)"),
    E("md_player_seek", "media", ["ArrowLeft", "ArrowRight", "Home", "End"], "Плеер озвучки: −5 / +5 секунд, в начало, в конец", "Voice player: −5 / +5 seconds, start, end"),
    E("md_player_menu", "media", ["ArrowDown", "ArrowUp", "Escape"], "Плеер озвучки: меню скорости и действий", "Voice player: speed and action menu"),
    E("md_gallery", "media", ["ArrowLeft", "ArrowRight", "Escape"], "Карточка товара: листать фото / закрыть", "Product card: browse photos / close"),
    E("md_cards", "media", ["ArrowLeft", "ArrowRight", "Home", "End"], "Ряд карточек товаров: перемещение", "Product card row: move"),
    E("md_chart", "media", ["ArrowLeft", "ArrowRight"], "График «Рефлексии»: соседний период", "Reflect chart: the neighbouring period"),
    E("md_computer_stop", "media", "Escape", "Computer Use и браузер агента: остановить сразу", "Computer Use and the agent browser: stop at once"),

    E("qa_send", "quick", ["Enter", "Shift+Enter"], "Отправить / новая строка", "Send / new line"),
    E("qa_escape", "quick", "Escape", "Шаг назад: списки → вопрос DFY → остановить ответ → закрыть окно", "One step back: lists → DFY question → stop the answer → close the window"),
    E("qa_history", "quick", ["ArrowUp", "ArrowDown"], "В пустом поле: недавние вопросы", "In an empty field: recent questions"),
    E("qa_new", "quick", "Ctrl+N", "Новый чат", "New chat"),
    E("qa_chats", "quick", "Ctrl+H", "Чаты быстрого доступа (Del — удалить)", "Quick Ask chats (Del — delete)"),
    E("qa_actions", "quick", "Ctrl+K", "Действия: всё второстепенное с поиском", "Actions: everything secondary, searchable"),
    E("qa_clear", "quick", "Ctrl+Shift+K", "Очистить окно", "Clear the window"),
    E("qa_dfy", "quick", "Ctrl+Enter", "Сделать за меня — передать агенту Do For You", "Do it for me — hand over to the Do For You agent"),
    E("qa_open", "quick", "Ctrl+Shift+Enter", "Открыть разговор в MemuAI", "Open the conversation in MemuAI"),
    E("qa_pin", "quick", "Ctrl+P", "Закрепить или открепить окно", "Pin or unpin the window"),
    E("qa_routing", "quick", "Alt+R", "Модель: авто / быстрая / умная", "Model: auto / fast / smart"),
    E("qa_advanced", "quick", "Ctrl+Shift+A", "Спросить продвинутую модель ещё раз", "Ask the advanced model again"),
    E("qa_rows", "quick", ["Alt+1…Alt+9"], "Строки списка по номеру (например «Кратко» для страницы)", "List rows by number (e.g. “Brief” for a page)"),
    E("qa_dock", "quick", ["Ctrl+Alt+ArrowLeft", "Ctrl+Alt+ArrowRight", "Ctrl+Alt+ArrowUp", "Ctrl+Alt+ArrowDown"], "Прижать окно к краю экрана", "Dock the window to a screen edge"),
    E("qa_copy", "quick", "Ctrl+Shift+C", "Скопировать ответ", "Copy the answer"),
    E("qa_shot", "quick", "Ctrl+Shift+S", "Прикрепить снимок экрана", "Attach a screenshot"),
    E("qa_model", "quick", "Alt+M", "Выбрать модель для этого окна", "Choose the model for this window"),
    E("qa_calc", "quick", "Tab", "Подставить результат калькулятора в поле", "Put the calculator result into the field"),
    E("qa_voice", "quick", "Ctrl+Space", "Держать — говорить (push-to-talk)", "Hold — talk (push-to-talk)", { hold: true }),
    E("qa_page_off", "quick", "Backspace", "В пустом поле: убрать прикреплённую страницу", "In an empty field: drop the attached page"),
    E("qa_lists", "quick", ["ArrowUp", "ArrowDown", "Enter", "Escape"], "Списки моделей, чатов и действий", "Model, chat and action lists"),

    E("qt_region", "quick_tools", "Ctrl+Shift+X", "Спросить про область экрана (Enter — весь экран, Esc — отмена)", "Ask about a screen region (Enter — whole screen, Esc — cancel)"),
    E("qt_clipboard", "quick_tools", "Alt+V", "История буфера обмена", "Clipboard history"),
    E("qt_sticker", "quick_tools", "Ctrl+Shift+P", "Закрепить ответ стикером", "Pin the answer as a sticker"),
    E("qt_strip", "quick_tools", ["Alt+1…Alt+4"], "Кнопки полоски действий под ответом", "The action strip buttons under an answer"),
    E("qt_clip_keys", "quick_tools", ["Ctrl+C", "Ctrl+P", "Shift+Delete", "Tab"], "Буфер: копировать, закрепить, удалить, спросить", "Clipboard: copy, pin, delete, ask"),
    E("qt_snippets", "quick_tools", ["Ctrl+N", "Ctrl+E", "Ctrl+Enter"], "Сниппеты: новый, изменить, сохранить", "Snippets: new, edit, save"),
    E("qt_snippet_inline", "quick_tools", ["Tab", "Enter"], "«;слово» в поле: подставить сниппет", "“;keyword” in the field: insert the snippet"),
    E("qt_selection", "quick_tools", ["Alt+Enter", "Alt+1…Alt+9", "ArrowLeft", "ArrowRight"], "Выделенный текст: заменить ответом, действия по номеру, выбор кнопки", "Selected text: replace with the answer, actions by number, pick a button"),
    E("qt_layer", "quick_tools", ["Ctrl+B", "Ctrl+E"], "Панели и кнопки: показать / скрыть, расставить", "Panels and buttons: show / hide, arrange"),
    E("qt_layer_edit", "quick_tools", ["Alt+ArrowLeft", "Alt+ArrowRight", "Alt+ArrowUp", "Alt+ArrowDown", "Delete"], "Расстановка: сдвинуть или убрать элемент", "Arranging: move or remove an item"),
    E("qt_buttons", "quick_tools", ["1…9", "Enter"], "Кнопки под полем: запустить по номеру", "Buttons under the field: run by number"),
    E("qt_item_menu", "quick_tools", ["Shift+F10", "ContextMenu"], "Меню панели или кнопки", "Panel or button menu"),
    E("qt_sticker_window", "quick_tools", ["Ctrl+C", "Ctrl+W", "Ctrl+Enter", "Escape"], "Окно стикера: копировать, закрыть, сохранить правку, отменить", "Sticker window: copy, close, save the edit, cancel"),

    E("g_quick", "global", "Alt+Space", "Открыть быстрый доступ", "Open Quick Ask", { external: "quick_ask_hotkey", page: "quick" }),
    E("g_quick_escape", "global", "Escape", "Закрыть открытое окно быстрого доступа из любой программы (кроме закреплённого)", "Close an open Quick Ask window from any program (unless pinned)"),
    E("g_region", "global", "", "Спросить про область экрана", "Ask about a screen region", { external: "region_hotkey", page: "quick" }),
    E("g_clipboard", "global", "", "История буфера обмена", "Clipboard history", { external: "clipboard_hotkey", page: "quick" }),
    E("g_commands", "global", "", "Свои команды с глобальным сочетанием", "Custom commands with a global hotkey", { external: "commands", page: "quick" }),
    E("g_buttons", "global", "", "Кнопки быстрого доступа с глобальным сочетанием", "Quick buttons with a global hotkey", { external: "buttons", page: "quick" }),
  ];

  const KEY_ALIASES = {
    esc: "Escape", escape: "Escape", del: "Delete", delete: "Delete", ins: "Insert", insert: "Insert",
    return: "Enter", enter: "Enter", space: "Space", spacebar: "Space", " ": "Space", tab: "Tab",
    backspace: "Backspace", home: "Home", end: "End", pgup: "PageUp", pageup: "PageUp", pgdn: "PageDown", pagedown: "PageDown",
    left: "ArrowLeft", arrowleft: "ArrowLeft", "←": "ArrowLeft", right: "ArrowRight", arrowright: "ArrowRight", "→": "ArrowRight",
    up: "ArrowUp", arrowup: "ArrowUp", "↑": "ArrowUp", down: "ArrowDown", arrowdown: "ArrowDown", "↓": "ArrowDown",
    comma: "Comma", ",": "Comma", period: "Period", ".": "Period", slash: "Slash", "/": "Slash", backslash: "Backslash", "\\": "Backslash",
    semicolon: "Semicolon", ";": "Semicolon", quote: "Quote", "'": "Quote", bracketleft: "BracketLeft", "[": "BracketLeft",
    bracketright: "BracketRight", "]": "BracketRight", minus: "Minus", "-": "Minus", equal: "Equal", "=": "Equal", plus: "Equal",
    backquote: "Backquote", "`": "Backquote", "~": "Backquote", contextmenu: "ContextMenu", menu: "ContextMenu",
  };
  const MOD_ALIASES = { ctrl: "Ctrl", control: "Ctrl", cmd: "Ctrl", command: "Ctrl", meta: "Win", win: "Win", super: "Win",
    alt: "Alt", option: "Alt", shift: "Shift" };
  const MOD_ORDER = ["Ctrl", "Alt", "Shift", "Win"];
  const DISPLAY = { ArrowLeft: "←", ArrowRight: "→", ArrowUp: "↑", ArrowDown: "↓", Comma: ",", Period: ".", Slash: "/",
    Backslash: "\\", Semicolon: ";", Quote: "'", BracketLeft: "[", BracketRight: "]", Minus: "-", Equal: "=",
    Backquote: "`", Escape: "Esc", Delete: "Del", Insert: "Ins", PageUp: "PgUp", PageDown: "PgDn", ContextMenu: "Menu" };

  function canonicalKey(raw) {
    const text = String(raw == null ? "" : raw);
    const trimmed = text.trim();
    if (!trimmed && text === " ") return "Space";
    const low = trimmed.toLowerCase();
    if (KEY_ALIASES[low]) return KEY_ALIASES[low];
    if (/^key[a-z]$/i.test(trimmed)) return trimmed.slice(3).toUpperCase();
    if (/^digit[0-9]$/i.test(trimmed)) return trimmed.slice(5);
    if (/^numpad[0-9]$/i.test(trimmed)) return trimmed.slice(6);
    if (/^f([1-9]|1[0-9]|2[0-4])$/i.test(trimmed)) return trimmed.toUpperCase();
    if (/^[a-z0-9]$/i.test(trimmed)) return trimmed.toUpperCase();
    if (trimmed === "@") return "@";
    return trimmed ? trimmed[0].toUpperCase() + trimmed.slice(1) : "";
  }

  function normalize(combo) {
    const text = String(combo == null ? "" : combo).trim();
    if (!text) return "";
    if (text.includes("…")) return text;

    const parts = text.replace(/\s+/g, "").replace(/\+\+$/, "+Plus").split("+").filter((part, i, all) => part || i === all.length - 1);
    const mods = new Set();
    let key = "";
    for (const part of parts) {
      const mod = MOD_ALIASES[part.toLowerCase()];
      if (mod) mods.add(mod); else key = canonicalKey(part);
    }
    return [...MOD_ORDER.filter((m) => mods.has(m)), key].filter(Boolean).join("+");
  }

  function parse(combo) {
    const norm = normalize(combo);
    const parts = norm ? norm.split("+") : [];
    const key = parts.length && !MOD_ORDER.includes(parts[parts.length - 1]) ? parts[parts.length - 1] : "";
    return { ctrl: parts.includes("Ctrl"), alt: parts.includes("Alt"), shift: parts.includes("Shift"),
      win: parts.includes("Win"), key, combo: norm };
  }

  function eventKey(e) {
    const code = String(e && e.code || "");
    if (/^Key[A-Z]$/.test(code)) return code.slice(3);
    if (/^Digit[0-9]$/.test(code)) return code.slice(5);
    if (/^Numpad[0-9]$/.test(code)) return code.slice(6);
    if (code === "NumpadAdd") return "Equal";
    if (code === "NumpadSubtract") return "Minus";
    if (code === "NumpadDivide") return "Slash";
    if (code === "NumpadEnter") return "Enter";
    if (/^(Comma|Period|Slash|Backslash|Semicolon|Quote|BracketLeft|BracketRight|Minus|Equal|Backquote|Space|Enter|Escape|Tab|Backspace|Delete|Insert|Home|End|PageUp|PageDown|ContextMenu)$/.test(code)) return code;
    if (/^Arrow(Left|Right|Up|Down)$/.test(code)) return code;
    if (/^F([1-9]|1[0-9]|2[0-4])$/.test(code)) return code;
    return canonicalKey(e && e.key);
  }

  function eventCombo(e) {
    const key = eventKey(e);
    if (!key || ["Control", "Alt", "Shift", "Meta", "OS", "AltGraph"].includes(key)) return "";
    return [e.ctrlKey ? "Ctrl" : "", e.altKey ? "Alt" : "", e.shiftKey ? "Shift" : "", e.metaKey ? "Win" : "", key].filter(Boolean).join("+");
  }

  function matchesCombo(combo, e) {
    const spec = parse(combo);
    if (!spec.key || !e) return false;

    const ctrl = !!(e.ctrlKey || e.metaKey);
    if (ctrl !== spec.ctrl || !!e.altKey !== spec.alt || !!e.shiftKey !== spec.shift) return false;
    const actual = eventKey(e);
    if (actual === spec.key) return true;

    return canonicalKey(e.key) === spec.key && !/^(Key|Digit)/.test(String(e.code || ""));
  }

  function caps(combo) {
    const text = String(combo || "");
    if (!text) return [];
    if (text.includes("…")) return text.split("…").map((part, index) => (index ? ["…", ...caps(part)] : caps(part))).flat();
    return normalize(text).split("+").map((part) => DISPLAY[part] || part);
  }
  function label(combo) { return caps(combo).join("+").replace(/\+…\+/g, "…"); }

  const byId = new Map(REGISTRY.map((row) => [row.id, row]));
  const extra = [];
  const externals = {};

  function prefs() {
    try { return (typeof pref === "function" ? pref("shortcuts", {}) : {}) || {}; } catch (_) { return {}; }
  }
  function entry(id) { return byId.get(id) || extra.find((row) => row.id === id) || null; }
  function defaultOf(id) { const row = entry(id); return row ? normalize(row.keys[0] || "") : ""; }
  function isCustomized(id) { return Object.prototype.hasOwnProperty.call(prefs(), id) && normalize(prefs()[id]) !== defaultOf(id); }

  function value(id, fallback) {
    const saved = prefs();
    if (Object.prototype.hasOwnProperty.call(saved, id)) return normalize(saved[id]);
    const row = entry(id);
    if (row) return normalize(row.keys[0] || "");
    return normalize(fallback || "");
  }
  function match(id, e, fallback) {
    const combo = value(id, fallback);
    return !!combo && matchesCombo(combo, e);
  }
  function all() { return REGISTRY.concat(extra); }

  function register(rows) {
    for (const row of rows || []) {
      if (!row || !row.id || entry(row.id)) continue;
      extra.push({ group: "main", keys: [""], ru: row.en || row.id, en: row.ru || row.id, custom: false, scope: row.group || "main", ...row,
        keys: Array.isArray(row.keys) ? row.keys : [row.keys || ""] });
    }
    if (root.document) rerender();
  }

  function conflicts(overrides) {
    const saved = overrides || prefs();
    const live = [];
    for (const row of all()) {
      const combos = row.custom
        ? [Object.prototype.hasOwnProperty.call(saved, row.id) ? normalize(saved[row.id]) : normalize(row.keys[0] || "")]
        : (row.external ? [].concat(externals[row.external] || []).map(normalize) : row.keys.map(normalize));

      for (const combo of combos) {
        if (combo && !combo.includes("…") && (combo.includes("+") || /^F([1-9]|1[0-9]|2[0-4])$/.test(combo))) live.push({ row, combo });
      }
    }
    const found = [];
    for (let i = 0; i < live.length; i += 1) {
      for (let j = i + 1; j < live.length; j += 1) {
        const a = live[i], b = live[j];
        if (a.combo !== b.combo || a.row.id === b.row.id) continue;
        if (!(a.row.custom || b.row.custom)) continue;
        const scopesA = SCOPES[a.row.scope] || [a.row.scope];
        if (!scopesA.includes(b.row.scope)) continue;
        found.push({ combo: a.combo, a: a.row.id, b: b.row.id });
      }
    }
    return found;
  }

  const RESERVED = {
    "Alt+F4": ["закрывает окно в Windows", "closes the window in Windows"],
    "Alt+Tab": ["переключает окна Windows", "switches Windows windows"],
    "Ctrl+Alt+Delete": ["системное сочетание Windows", "a Windows system combination"],
    "Ctrl+Escape": ["открывает меню «Пуск»", "opens the Start menu"],
    "Ctrl+Shift+Escape": ["открывает диспетчер задач", "opens Task Manager"],
    "Ctrl+C": ["копирование текста", "copying text"], "Ctrl+V": ["вставка текста", "pasting text"],
    "Ctrl+X": ["вырезание текста", "cutting text"], "Ctrl+A": ["выделить всё", "select all"],
    "Ctrl+Z": ["отмена в полях ввода", "undo in fields"], "Ctrl+Y": ["повтор в полях ввода", "redo in fields"],
    "Ctrl+R": ["перезагрузка интерфейса", "reloading the interface"], "Escape": ["Esc закрывает окна и останавливает ответ", "Esc closes windows and stops the answer"],
    "Enter": ["отправка сообщения", "sending a message"], "Tab": ["переход по элементам", "moving between controls"],
  };

  function problemOf(combo, lang) {
    const en = lang === "en";
    const spec = parse(combo);
    if (!spec.key) return en ? "Press a key together with the modifiers." : "Нажмите клавишу вместе с модификаторами.";
    if (spec.win) return en ? "Windows reserves Win+… combinations." : "Сочетания с Win занимает Windows.";
    const reserved = RESERVED[spec.combo];
    if (reserved) return (en ? "Taken: " : "Занято: ") + reserved[en ? 1 : 0];
    const bare = !spec.ctrl && !spec.alt && !/^F([1-9]|1[0-9]|2[0-4])$/.test(spec.key);
    if (bare) return en ? "Without Ctrl or Alt the key would fire while you type. Add a modifier." : "Без Ctrl или Alt клавиша сработает во время набора текста. Добавьте модификатор.";
    return "";
  }

  function setExternal(name, combos) { externals[name] = [].concat(combos || []).filter(Boolean); }

  const api = { GROUPS, SCOPES, REGISTRY, normalize, setExternal, parse, caps, label, eventKey, eventCombo, matchesCombo,
    entry, defaultOf, value, match, all, conflicts, problemOf, register };

  if (typeof module === "object" && module && module.exports) module.exports = api;
  if (!root || !root.document) return;

  const doc = root.document;
  const en = () => (typeof PG_LANG !== "undefined" && PG_LANG === "en");
  const L = (ru, english) => (en() ? english : ru);
  const T = (row) => (en() ? row.en : row.ru);
  const ico = (name) => (typeof icon === "function" ? icon(name) : "");
  const escHtml = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const $ = (sel, node) => (node || doc).querySelector(sel);
  const $$ = (sel, node) => Array.from((node || doc).querySelectorAll(sel));
  const say = (text, kind = "ok") => { try { toast(text, kind); } catch (_) {  } };

  function kbd(combo, extraClass = "") {
    const list = caps(combo);
    if (!list.length) return `<span class="hk-none">${L("не задано", "not set")}</span>`;
    return `<span class="hk-caps ${extraClass}">${list.map((cap) => cap === "…" ? `<span class="hk-range">…</span>` : `<kbd class="hk-cap">${escHtml(cap)}</kbd>`).join("")}</span>`;
  }
  function keysHtml(row, combo) {
    if (row.custom) return kbd(combo);
    const keys = row.keys.filter(Boolean);
    if (!keys.length) return `<span class="hk-none">${L("не задано", "not set")}</span>`;
    return keys.map((k) => kbd(k)).join(`<span class="hk-or">${keys.length > 3 ? "" : "/"}</span>`);
  }

  function save(id, combo) {
    const values = { ...prefs() };
    const norm = normalize(combo);
    if (norm === defaultOf(id)) delete values[id]; else values[id] = norm;
    if (typeof savePreferences === "function") savePreferences({ shortcuts: values });
    else if (typeof S !== "undefined" && S.preferences) S.preferences.shortcuts = values;
    rerender();
  }
  function resetAll() {
    if (typeof savePreferences === "function") savePreferences({ shortcuts: {} });
    rerender();
  }

  function visibleChats() {
    return $$("#chats-pinned [data-chat], #chat-folders .chat-folder:not(.collapsed) [data-chat], #chats-list [data-chat]").map((el) => el.dataset.chat);
  }
  function stepChat(delta) {
    const ids = visibleChats();
    if (!ids.length || typeof openChat !== "function") return;
    const current = typeof S !== "undefined" ? S.currentId : "";
    let at = ids.indexOf(current);
    at = at < 0 ? (delta > 0 ? 0 : ids.length - 1) : Math.max(0, Math.min(ids.length - 1, at + delta));
    if (ids[at] && ids[at] !== current) openChat(ids[at]);
  }
  function zoom(direction) {
    const steps = [85, 90, 100, 110, 125];
    const now = Number((typeof pref === "function" && pref("font_scale", 100)) || 100);
    let next = 100;
    if (direction) {
      const at = steps.findIndex((n) => n >= now);
      const index = at < 0 ? steps.length - 1 : (steps[at] === now ? at + direction : (direction > 0 ? at : at - 1));
      next = steps[Math.max(0, Math.min(steps.length - 1, index))];
    }
    if (next === now) { say(L(`Размер интерфейса: ${now} %`, `Interface size: ${now}%`), "info"); return; }
    if (typeof savePreferences === "function") savePreferences({ font_scale: next });
    if (typeof S !== "undefined" && S.preferences) S.preferences.font_scale = next;
    if (typeof applyAppearancePrefs === "function") applyAppearancePrefs();
    say(L(`Размер интерфейса: ${next} %`, `Interface size: ${next}%`));
  }
  function copyLast() {
    if (typeof S === "undefined" || typeof copyMessage !== "function") return;
    for (let i = S.messages.length - 1; i >= 0; i -= 1) {
      if (S.messages[i] && S.messages[i].role === "assistant" && !S.messages[i].internal) { copyMessage(i); return; }
    }
    say(L("Ответов пока нет", "No answers yet"), "warn");
  }
  const click = (sel) => () => { const node = $(sel); if (node && !node.disabled) node.click(); };
  const ACTIONS = {
    mode_chat: click("#nav-chat"), mode_work: click("#nav-work"), mode_code: click("#nav-code"),
    mode_schedule: click("#nav-sched"), mode_skills: click("#nav-skills"),
    chat_prev: () => stepChat(-1), chat_next: () => stepChat(1),
    rename_chat: () => { if (typeof S !== "undefined" && typeof renameChat === "function" && S.chats.some((c) => c.id === S.currentId)) renameChat(S.currentId); },
    pin_chat: () => { if (typeof S !== "undefined" && typeof togglePin === "function") togglePin(S.currentId); },
    copy_last: copyLast,
    attach: click("#file-input"),
    dictation: click("#btn-mic"),
    live: () => { const b = $("#btn-live"); if (b && b.offsetParent) b.click(); else if (typeof openLiveCall === "function") openLiveCall(); },
    docs: () => { if (root.MemuDocs) root.MemuDocs.open(); },
    zoom_in: () => zoom(1), zoom_out: () => zoom(-1), zoom_reset: () => zoom(0),
    theme: () => { if (typeof setTheme === "function" && typeof S !== "undefined") setTheme(S.theme === "dark" ? "light" : "dark"); },
  };

  function handleMain(e) {
    if (!e || e.defaultPrevented || state.recording) return false;
    const target = e.target && e.target.closest ? e.target : null;
    for (const id of Object.keys(ACTIONS)) {
      const combo = value(id);
      if (!combo || !matchesCombo(combo, e)) continue;
      const spec = parse(combo);

      if (!spec.ctrl && target && target.closest("input, textarea, select, [contenteditable=''], [contenteditable='true'], [data-panel-id], .mp-pop, [role='listbox'], [role='menu'], [role='tree'], [role='slider'], .code-lines")) return false;
      e.preventDefault();
      try { ACTIONS[id](); } catch (error) { console.warn("hotkey", id, error); }
      return true;
    }
    return false;
  }

  const state = { recording: null, query: "", filter: "all", host: null };

  root.addEventListener("keydown", (e) => {
    const rec = state.recording;
    if (!rec) return;

    if (e.key === "Tab" && !e.ctrlKey && !e.altKey) { stopRecording(true); return; }
    e.preventDefault();
    e.stopImmediatePropagation();
    if (["Control", "Alt", "Shift", "Meta", "OS", "AltGraph"].includes(e.key)) {
      rec.preview([e.ctrlKey && "Ctrl", e.altKey && "Alt", e.shiftKey && "Shift"].filter(Boolean).join("+") + "+…");
      return;
    }
    if (e.key === "Escape" && !e.ctrlKey && !e.altKey && !e.shiftKey) { stopRecording(); return; }
    if ((e.key === "Backspace" || e.key === "Delete") && !e.ctrlKey && !e.altKey && !e.shiftKey) { finishRecording(""); return; }
    const combo = eventCombo(e);
    if (combo) finishRecording(combo);
  }, true);

  function startRecording(id, button) {

    stopRecording(true);
    const row = entry(id);
    if (!row || !row.custom || !button.isConnected) return;
    button.classList.add("recording");
    button.setAttribute("aria-pressed", "true");
    button.removeAttribute("title");
    button.innerHTML = `<span class="hk-rec-dot"></span><span class="hk-rec-text">${L("Нажмите клавиши…", "Press keys…")}</span>`;
    const hint = button.closest("[data-hk-row]")?.querySelector(".hk-row-hint");
    if (hint) { hint.textContent = L("Esc — отмена, Backspace — отключить", "Esc — cancel, Backspace — disable"); hint.className = "hk-row-hint info"; }
    state.recording = { id, button, preview: (text) => { const t = button.querySelector(".hk-rec-text"); if (t) t.textContent = text; } };
    const blur = () => setTimeout(() => { if (state.recording && state.recording.button === button) stopRecording(); }, 150);
    button.addEventListener("blur", blur, { once: true });
  }
  function stopRecording(rowOnly = false) {
    const rec = state.recording;
    state.recording = null;
    if (!rec) return;
    if (rowOnly) refreshRow(rec.id); else rerender();
  }

  function refreshRow(id) {
    const old = state.host && $(`[data-hk-row="${CSS.escape(id)}"]`, state.host);
    const row = entry(id);
    if (!old || !row) return;
    const hadFocus = old.contains(doc.activeElement);
    const box = doc.createElement("div");
    box.innerHTML = rowHtml(row, conflicts());
    const next = box.firstElementChild;
    old.replaceWith(next);
    if (hadFocus) $("[data-hk-record]", next)?.focus({ preventScroll: true });
  }
  function finishRecording(combo) {
    const rec = state.recording;
    if (!rec) return;
    state.recording = null;
    const norm = normalize(combo);
    if (norm) {
      const problem = problemOf(norm, en() ? "en" : "ru");
      const hardStop = /^(Alt\+F4|Ctrl\+Alt\+Delete|Ctrl\+Escape|Ctrl\+Shift\+Escape|Alt\+Tab)$/.test(norm) || parse(norm).win
        || (!parse(norm).ctrl && !parse(norm).alt && !/^F\d/.test(parse(norm).key));
      if (hardStop) { say(problem, "warn"); rerender(); return; }
      if (problem) say(problem, "warn");
      const clash = conflicts({ ...prefs(), [rec.id]: norm }).filter((c) => c.a === rec.id || c.b === rec.id);
      if (clash.length) {
        const other = entry(clash[0].a === rec.id ? clash[0].b : clash[0].a);
        say(L(`${label(norm)} уже занято: «${other ? T(other) : ""}». Сочетание сохранено — поменяйте одно из них.`,
          `${label(norm)} is already used by “${other ? T(other) : ""}”. Saved — change one of them.`), "warn");
      }
    }
    save(rec.id, norm);
    if (!norm) say(L("Сочетание отключено", "Shortcut disabled"), "info");
  }

  let globalCache = null;
  async function loadGlobals() {
    const get = async (url) => { try { const r = await fetch(url); return r.ok ? await r.json() : null; } catch (_) { return null; } };
    const [config, features, assistant, canvas] = await Promise.all([
      get("/api/quick/config"), get("/api/quick/features"), get("/api/assistant/config?lang=" + (en() ? "en" : "ru")),
      get("/api/quick/canvas?lang=" + (en() ? "en" : "ru")),
    ]);
    const settings = (features && features.settings) || {};
    const commands = ((assistant && assistant.commands) || []).filter((c) => c && c.hotkey && c.enabled !== false)
      .map((c) => ({ combo: normalize(c.hotkey), title: c.name || c.title || c.phrase || c.id || "" }));
    const buttons = ((canvas && canvas.actions) || []).filter((a) => a && a.hotkey && a.global)
      .map((a) => ({ combo: normalize(a.hotkey), title: a.title || a.label || a.name || a.id || "" }));
    setExternal("quick_ask_hotkey", config && config.prefs ? config.prefs.quick_ask_hotkey || "Alt+Space" : "Alt+Space");
    setExternal("region_hotkey", settings.region_hotkey || "");
    setExternal("clipboard_hotkey", settings.clipboard_hotkey || "");
    setExternal("commands", commands.map((c) => c.combo));
    setExternal("buttons", buttons.map((b) => b.combo));
    globalCache = {
      quick_ask_hotkey: config && config.prefs ? normalize(config.prefs.quick_ask_hotkey || "Alt+Space") : "Alt+Space",
      quick_status: config && config.status && config.status.hotkey,
      region_hotkey: normalize(settings.region_hotkey || ""), clipboard_hotkey: normalize(settings.clipboard_hotkey || ""),
      commands, buttons, loaded: true,
    };
    return globalCache;
  }
  function globalValue(row) {
    const g = globalCache || {};
    if (row.external === "quick_ask_hotkey") return g.quick_ask_hotkey || "Alt+Space";
    if (row.external === "region_hotkey") return g.region_hotkey || "";
    if (row.external === "clipboard_hotkey") return g.clipboard_hotkey || "";
    return "";
  }

  function matchesQuery(row, combo, q) {
    if (!q) return true;
    const hay = `${row.ru} ${row.en} ${row.id} ${(row.keys || []).join(" ")} ${combo} ${label(combo)} ${caps(combo).join(" ")}`.toLowerCase();
    return q.split(/\s+/).filter(Boolean).every((word) => hay.includes(word));
  }

  function rowHtml(row, clashes) {
    const combo = row.custom ? value(row.id) : (row.external ? globalValue(row) : normalize(row.keys[0] || ""));
    const modified = row.custom && isCustomized(row.id);
    const clash = clashes.filter((c) => c.a === row.id || c.b === row.id);
    const other = clash.length ? entry(clash[0].a === row.id ? clash[0].b : clash[0].a) : null;
    const problem = row.custom && combo ? problemOf(combo, en() ? "en" : "ru") : "";
    let hint = "";
    if (other) hint = `<span class="hk-row-hint warn">${ico("bell")} ${escHtml(L("Конфликт с «", "Conflicts with “") + T(other) + L("»", "”"))}</span>`;
    else if (problem) hint = `<span class="hk-row-hint info">${escHtml(problem)}</span>`;
    else if (row.custom && !combo) hint = `<span class="hk-row-hint info">${L("Отключено", "Disabled")}</span>`;
    else hint = `<span class="hk-row-hint"></span>`;
    let control;
    if (row.custom) {
      control = `<button type="button" class="hk-record ${other ? "conflict" : ""}" data-hk-record="${escHtml(row.id)}" aria-pressed="false" aria-label="${escHtml(T(row) + ": " + (label(combo) || L("не задано", "not set")) + ". " + L("Нажмите, чтобы изменить", "Click to change"))}" title="${escHtml(L("Нажмите и введите новое сочетание", "Click and press a new combination"))}">${kbd(combo)}</button>
        <span class="hk-row-tools">
        <button type="button" class="hk-icon-btn" data-hk-reset="${escHtml(row.id)}" ${modified ? "" : "hidden"} title="${escHtml(L("Вернуть по умолчанию: ", "Reset to default: ") + (label(defaultOf(row.id)) || L("не задано", "not set")))}" aria-label="${escHtml(L("Вернуть по умолчанию", "Reset to default"))}">${ico("undo")}</button>
        <button type="button" class="hk-icon-btn hk-clear" data-hk-clear="${escHtml(row.id)}" ${combo ? "" : "hidden"} title="${escHtml(L("Отключить сочетание", "Disable the shortcut"))}" aria-label="${escHtml(L("Отключить сочетание", "Disable the shortcut"))}">${ico("close")}</button></span>`;
    } else if (row.external) {
      let shown = kbd(combo);
      if (row.external === "commands" || row.external === "buttons") {
        const list = (globalCache && globalCache[row.external]) || [];
        shown = list.length ? list.slice(0, 6).map((item) => `<span class="hk-ext-item">${kbd(item.combo)}<span class="hk-ext-title">${escHtml(item.title)}</span></span>`).join("")
          + (list.length > 6 ? `<span class="hk-none">+${list.length - 6}</span>` : "")
          : `<span class="hk-none">${globalCache ? L("нет", "none") : "…"}</span>`;
      }
      control = `<span class="hk-ext">${shown}</span><button type="button" class="pbtn ghost sx-btn-sm" data-hk-open="${escHtml(row.page || "quick")}">${L("Изменить", "Change")}</button>`;
    } else {
      control = `<span class="hk-fixed">${keysHtml(row)}</span>`;
    }
    return `<div class="hk-row ${row.custom ? "custom" : "fixed"} ${modified ? "modified" : ""} ${other ? "has-conflict" : ""}" data-hk-row="${escHtml(row.id)}">
      <div class="hk-row-text"><span class="hk-row-label">${escHtml(T(row))}${row.added === 29 ? ` <span class="hk-new">${L("новое", "new")}</span>` : ""}${modified ? ` <span class="hk-mod">${L("изменено", "changed")}</span>` : ""}${row.hold ? ` <span class="hk-mod">${L("удерживать", "hold")}</span>` : ""}</span>${hint}</div>
      <div class="hk-row-keys">${control}</div>
    </div>`;
  }

  function render() {
    const host = state.host;
    if (!host || !host.isConnected) return;
    if (!$(".hk-shell", host)) {
      host.innerHTML = `<div class="hk-shell">
        <div class="hk-hero"></div>
        <div class="hk-toolbar">
          <label class="hk-search">${ico("search")}<input type="search" id="hk-query" value="${escHtml(state.query)}" placeholder="${escHtml(L("Найти действие или клавишу…", "Find an action or key…"))}" title="${escHtml(L("Найти действие или клавишу, например «Ctrl+K» или «модель»", "Find an action or a key, e.g. “Ctrl+K” or “model”"))}" spellcheck="false" autocomplete="off"></label>
          <div class="hk-chips" role="tablist"></div>
        </div>
        <div class="hk-banner-slot"></div>
        <div class="hk-groups"></div>
      </div>`;
      bindSettings(host);
    }
    const q = state.query.trim().toLowerCase();
    const clashes = conflicts();
    const rows = all().filter((row) => {
      const combo = row.custom ? value(row.id) : (row.external ? globalValue(row) : row.keys.join(" "));
      if (!matchesQuery(row, combo, q)) return false;
      if (state.filter === "custom") return row.custom;
      if (state.filter === "changed") return row.custom && isCustomized(row.id);
      if (state.filter === "conflicts") return clashes.some((c) => c.a === row.id || c.b === row.id);
      return true;
    });
    const total = all().length, customCount = all().filter((r) => r.custom).length;
    const changed = all().filter((r) => r.custom && isCustomized(r.id)).length;
    const chips = [["all", L("Все", "All"), total], ["custom", L("Настраиваемые", "Customizable"), customCount],
      ["changed", L("Изменённые", "Changed"), changed], ["conflicts", L("Конфликты", "Conflicts"), clashes.length]];
    const banner = clashes.length ? `<div class="hk-banner warn">${ico("bell")}<div><b>${L("Есть конфликты сочетаний", "Some shortcuts conflict")}</b>
      <span>${clashes.slice(0, 4).map((c) => `${label(c.combo)}: «${escHtml(T(entry(c.a)))}» ${L("и", "and")} «${escHtml(T(entry(c.b)))}»`).join("; ")}${clashes.length > 4 ? " …" : ""}</span></div>
      <button type="button" class="pbtn ghost sx-btn-sm" data-hk-filter="conflicts">${L("Показать", "Show")}</button></div>` : "";
    const groups = GROUPS.map((group) => {
      const list = rows.filter((row) => row.group === group.id);
      if (!list.length) return "";
      return `<section class="hk-group" data-hk-group="${group.id}">
        <header class="hk-group-head"><span class="hk-group-icon">${ico(group.icon)}</span><div><h4>${escHtml(en() ? group.en : group.ru)}</h4><p>${escHtml(en() ? group.desc_en : group.desc_ru)}</p></div><span class="hk-count">${list.length}</span></header>
        <div class="hk-rows">${list.map((row) => rowHtml(row, clashes)).join("")}</div>
      </section>`;
    }).join("");
    $(".hk-hero", host).innerHTML = `
          <div class="hk-hero-text"><b>${L(`${total} сочетаний в одном месте`, `${total} shortcuts in one place`)}</b>
            <span>${L("Нажмите на сочетание и введите новое: Esc — отмена, Backspace — отключить.", "Click a shortcut and press a new one: Esc cancels, Backspace disables.")}</span></div>
          <div class="hk-hero-actions">
            <button type="button" class="pbtn ghost sx-btn-sm" data-hk-reset-all ${changed ? "" : "hidden"}>${ico("undo")} ${L("Сбросить все", "Reset all")}</button>
            <button type="button" class="pbtn ghost sx-btn-sm" data-hk-sheet>${ico("keyboard")} ${L("Шпаргалка", "Cheat sheet")} ${kbd("Ctrl+/", "hk-hint-caps")}</button>
          </div>`;
    $(".hk-chips", host).innerHTML = chips.map(([id, text, n]) => `<button type="button" role="tab" class="hk-chip ${state.filter === id ? "active" : ""} ${id === "conflicts" && n ? "warn" : ""}" aria-selected="${state.filter === id}" data-hk-filter="${id}">${escHtml(text)} <span>${n}</span></button>`).join("");
    $(".hk-banner-slot", host).innerHTML = banner;
    $(".hk-groups", host).innerHTML = groups || `<div class="hk-empty">${ico("search")}<b>${L("Ничего не найдено", "Nothing found")}</b><span>${L("Попробуйте другое слово или клавишу.", "Try another word or key.")}</span></div>`;
  }

  function bindSettings(host) {
    const input = $("#hk-query", host);
    if (input) input.oninput = () => { state.query = input.value; render(); };
    host.onclick = (ev) => {
      const b = ev.target.closest("button");
      if (!b || !host.contains(b)) return;
      const d = b.dataset;
      if (d.hkFilter) { state.filter = d.hkFilter; render(); return; }
      if (d.hkRecord) { if (state.recording && state.recording.id === d.hkRecord) stopRecording(true); else startRecording(d.hkRecord, b); return; }
      if (d.hkReset) {
        const prev = { ...prefs() }; delete prev[d.hkReset];
        if (typeof savePreferences === "function") savePreferences({ shortcuts: prev });
        rerender(); $(`[data-hk-record="${CSS.escape(d.hkReset)}"]`, host)?.focus({ preventScroll: true });
        say(L("Сочетание по умолчанию", "Default restored")); return;
      }
      if (d.hkClear) { const id = d.hkClear; save(id, ""); $(`[data-hk-record="${CSS.escape(id)}"]`, host)?.focus({ preventScroll: true }); say(L("Сочетание отключено", "Shortcut disabled"), "info"); return; }
      if (d.hkOpen) { if (typeof openSettingsPage === "function") openSettingsPage(d.hkOpen); return; }
      if (b.hasAttribute("data-hk-sheet")) { openSheet({ returnTo: "hotkeys" }); return; }
      if (b.hasAttribute("data-hk-reset-all")) {
        const n = all().filter((r) => r.custom && isCustomized(r.id)).length;
        if (!n) return;
        if (b.dataset.confirm !== "1") {
          b.dataset.confirm = "1";
          b.classList.add("danger");
          b.innerHTML = `${ico("undo")} ${L(`Точно сбросить ${n}?`, `Really reset ${n}?`)}`;
          setTimeout(() => { if (b.isConnected && b.dataset.confirm === "1") render(); }, 4000);
          return;
        }
        resetAll(); say(L("Все сочетания по умолчанию", "All shortcuts reset"));
      }
    };
  }

  function registerSearch() {
    const K = root.MemuSettingsKit;
    if (!K || !K.registerSearch) return;
    K.registerSearch(all().filter((row) => row.custom || row.external).map((row) => ({
      page: "hotkeys", label_ru: row.ru, label_en: row.en, desc_ru: "Горячая клавиша " + label(row.keys[0] || ""),
      desc_en: "Shortcut " + label(row.keys[0] || ""), keywords: `hotkey shortcut горячая клавиша сочетание ${row.id} ${(row.keys || []).join(" ")}`,
      anchor: `#pane-hotkeys [data-hk-row="${row.id}"]`,
    })));
  }

  function mountSettings(host) {
    const target = host || $("#hk-root") || $("#pane-hotkeys");
    if (!target) return;
    state.host = target;
    render();
    if (!globalCache) loadGlobals().then(() => render()).catch(() => {});
    else loadGlobals().then(() => render()).catch(() => {});
  }

  function rerender() {
    if (state.host && state.host.isConnected) render();
    if (sheetState.open) drawSheet();
  }

  const sheetState = { open: false, query: "" };

  function sheetRows(q) {
    return GROUPS.map((group) => {
      const list = all().filter((row) => row.group === group.id).filter((row) => {
        const combo = row.custom ? value(row.id) : (row.external ? globalValue(row) : row.keys.join(" "));
        return matchesQuery(row, combo, q) && (row.custom ? !!combo : true) && !(row.external && !globalValue(row) && !["commands", "buttons"].includes(row.external));
      });
      if (!list.length) return "";
      return `<section class="hk-sheet-group"><h4>${ico(group.icon)} ${escHtml(en() ? group.en : group.ru)}</h4>
        ${list.map((row) => {
          const combo = row.custom ? value(row.id) : (row.external ? globalValue(row) : "");
          const keys = row.custom || (row.external && combo) ? kbd(combo) : (row.external ? `<span class="hk-none">${L("в настройках", "in settings")}</span>` : keysHtml(row));
          return `<div class="hk-sheet-row"><span>${escHtml(T(row))}</span><span class="hk-sheet-keys">${keys}</span></div>`;
        }).join("")}</section>`;
    }).join("");
  }
  function drawSheet() {
    const box = $("#hk-sheet-body");
    if (!box) { sheetState.open = false; return; }
    const q = sheetState.query.trim().toLowerCase();
    box.innerHTML = sheetRows(q) || `<div class="hk-empty">${ico("search")}<b>${L("Ничего не найдено", "Nothing found")}</b></div>`;
  }
  function openSheet(options = {}) {
    if (typeof panel !== "function") return;
    const returnTo = options.returnTo || "";
    sheetState.query = "";
    panel(`
      <div class="hk-sheet" id="hk-sheet">
        <header class="hk-sheet-head">
          ${returnTo ? `<button class="panel-return" id="hk-sheet-back">${ico("back")} ${L("Назад к настройкам", "Back to settings")}</button>` : ""}
          <div class="hk-sheet-title"><h2>${L("Горячие клавиши MemuAI", "MemuAI keyboard shortcuts")}</h2>
            <p class="sub">${L("Все сочетания по разделам. Свои сочетания — в «Настройки → Горячие клавиши».", "Every shortcut by area. Your own combinations — Settings → Keyboard shortcuts.")}</p></div>
          <div class="hk-sheet-tools">
            <label class="hk-search small">${ico("search")}<input type="search" id="hk-sheet-query" placeholder="${escHtml(L("Найти…", "Find…"))}" autocomplete="off" spellcheck="false"></label>
            <button type="button" class="pbtn ghost sx-btn-sm" id="hk-sheet-print">${ico("export")} ${L("Печать", "Print")}</button>
            <button type="button" class="pbtn sx-btn-sm" id="hk-sheet-edit">${ico("settings")} ${L("Настроить", "Customize")}</button>
          </div>
        </header>
        <div class="hk-sheet-body" id="hk-sheet-body"></div>
      </div>`, "hk-sheet-panel");
    sheetState.open = true;
    if (!globalCache) loadGlobals().then(() => { if (sheetState.open) drawSheet(); }).catch(() => {});
    drawSheet();
    const q = $("#hk-sheet-query");
    if (q) { q.oninput = () => { sheetState.query = q.value; drawSheet(); }; setTimeout(() => q.focus(), 30); }

    const inSettings = () => !!doc.querySelector("#panel .sx-shell");
    const back = $("#hk-sheet-back");
    if (back) back.onclick = () => { sheetState.open = false; if (typeof closePanel === "function") closePanel(); if (!inSettings()) openSettings(); };
    $("#hk-sheet-print").onclick = () => {
      doc.body.classList.add("hk-printing");
      const done = () => { doc.body.classList.remove("hk-printing"); root.removeEventListener("afterprint", done); };
      root.addEventListener("afterprint", done);
      setTimeout(() => { try { root.print(); } catch (_) { done(); } setTimeout(done, 60000); }, 30);
    };
    $("#hk-sheet-edit").onclick = () => {
      sheetState.open = false;
      if (typeof closePanel === "function") closePanel();
      if (inSettings() && typeof openSettingsPage === "function") openSettingsPage("hotkeys"); else openSettings();
    };
  }
  function openSettings() {
    if (typeof showSettings !== "function") return;
    try { currentSettingsTab = "hotkeys"; } catch (_) {  }
    const result = showSettings(true);
    const go = () => { if (typeof openSettingsPage === "function") openSettingsPage("hotkeys"); };
    if (result && typeof result.then === "function") result.then(go); else go();
  }

  function summaryHtml() {
    const ids = ["new_chat", "search", "settings", "help", "sidebar", "focus", "composer", "models"];
    return `<div class="shortcuts-grid">${ids.map((id) => entry(id)).filter(Boolean).map((row) => `<div class="shortcut-item"><span>${escHtml(T(row))}</span>${kbd(value(row.id))}</div>`).join("")}</div>`;
  }

  const MENU_IDS = { new: "new_chat", temp: "temporary", export: "export", back: "back", forward: "forward", "copy-last": "copy_last",
    sidebar: "sidebar", focus: "focus", theme: "theme", search: "search", shortcuts: "help", settings: "settings" };
  function menuLabel(action, fallback) {
    const id = MENU_IDS[action];
    if (!id) return fallback || "";
    return label(value(id));
  }

  const ui = { mountSettings, openSheet, openSettings, handleMain, summaryHtml, menuLabel, rerender, loadGlobals };

  const MemuHotkeys = Object.freeze(Object.defineProperty(Object.assign({}, api, ui), "recording", { get: () => !!state.recording, enumerable: true }));
  root.MemuHotkeys = MemuHotkeys;
  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", registerSearch, { once: true });
  else setTimeout(registerSearch, 0);
})(typeof window !== "undefined" ? window : globalThis);
