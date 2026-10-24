// MemuNote — UI strings of Round 40 (r40-note): the editable spreadsheet grid and the multi-tab AI context.
// Merged into DICT by i18n.js; English contains no Cyrillic.
export const R40 = {
  ru: {
    sg_name_box: "Адрес ячейки — введите, например, B12 или Лист2!A1:C9", sg_formula: "Значение или формула (начните с =)",
    sg_read_only_hint: "Файлы .ods открываются только для чтения — редактирование доступно для .xlsx", sg_select_all: "Выделить всё",
    sg_fill_hint: "Потяните, чтобы заполнить: ряды чисел, формулы со сдвигом ссылок", sg_cached: "Функция не поддерживается встроенным вычислителем — показано значение, сохранённое в файле",
    sg_sum: "Сумма", sg_avg: "Среднее", sg_count: "Количество", sg_add_sheet: "Новый лист", sg_sheet_n: "Лист{n}",
    ai_ctx_tabs: "Вкладки", ai_ctx_hint: "Отметьте вкладки, которые ИИ увидит вместе с текущей, или напишите @имя в сообщении",
    ai_ctx_trunc: "усечено", ai_ctx_trunc_hint: "Документ большой: в контекст попадёт ~{pct}% ({kept} из {total} символов)",
    ai_ctx_budget: "≈{n} ток.", ai_ctx_budget_hint: "Примерный объём контекста. Общий бюджет — {max} символов: большие документы сокращаются пропорционально",
    ai_ctx_mention: "Вкладки", ai_ctx_none: "Других вкладок нет — откройте файлы, чтобы работать с несколькими сразу",
    ai_ctx_loading: "Читаю вкладки…",
    ai_op_set_cells: "Ячейки", ai_op_add_column: "Столбец", ai_op_add_sheet: "Новый лист",
    ai_hunk_no_sheet: "Нет такого листа", ai_hunk_bad_range: "Неверный диапазон", ai_hunk_no_doc: "Документ не найден среди выбранных вкладок",
    ai_hunk_read_only: "Документ только для чтения",
    ai_open_tab: "Открыть вкладку", ai_more_cells: "и ещё {n}",
  },
  en: {
    sg_name_box: "Cell address: type, e.g., B12 or Sheet2!A1:C9", sg_formula: "Value or formula (start with =)",
    sg_read_only_hint: ".ods files open read-only; editing is available for .xlsx", sg_select_all: "Select all",
    sg_fill_hint: "Drag to fill: number series, formulas with shifted references", sg_cached: "This function is not supported by the built-in engine: the value saved in the file is shown",
    sg_sum: "Sum", sg_avg: "Average", sg_count: "Count", sg_add_sheet: "New sheet", sg_sheet_n: "Sheet{n}",
    ai_ctx_tabs: "Tabs", ai_ctx_hint: "Tick the tabs the AI sees along with the current one, or type @name in the message",
    ai_ctx_trunc: "trimmed", ai_ctx_trunc_hint: "A large document: about {pct}% goes into the context ({kept} of {total} characters)",
    ai_ctx_budget: "≈{n} tok.", ai_ctx_budget_hint: "Approximate context size. Total budget: {max} characters; large documents are shortened proportionally",
    ai_ctx_mention: "Tabs", ai_ctx_none: "No other tabs: open files to work with several at once",
    ai_ctx_loading: "Reading tabs…",
    ai_op_set_cells: "Cells", ai_op_add_column: "Column", ai_op_add_sheet: "New sheet",
    ai_hunk_no_sheet: "No such sheet", ai_hunk_bad_range: "Bad range", ai_hunk_no_doc: "The document is not among the selected tabs",
    ai_hunk_read_only: "The document is read-only",
    ai_open_tab: "Open tab", ai_more_cells: "and {n} more",
  },
};
