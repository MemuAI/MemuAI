// MemuNote editor bundle: CodeMirror 6 + language data (lazy chunks) + minimap + markdown-it.
// Built by build_vendor.mjs into app/memunote/static/vendor/cm/ (ESM, code-split, minified).
export { EditorState, EditorSelection, Compartment, StateEffect, StateField, Transaction, Text, RangeSetBuilder, Prec } from "@codemirror/state";
export { EditorView, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter, drawSelection,
  dropCursor, rectangularSelection, crosshairCursor, highlightSpecialChars, Decoration, ViewPlugin, WidgetType,
  placeholder, scrollPastEnd, showPanel, highlightWhitespace, highlightTrailingWhitespace } from "@codemirror/view";
export { defaultKeymap, history, historyKeymap, indentWithTab, undo, redo, selectAll, toggleComment,
  copyLineDown, copyLineUp, moveLineUp, moveLineDown, deleteLine, indentMore, indentLess, cursorDocStart,
  cursorDocEnd, insertNewlineAndIndent, selectLine, transposeChars, undoDepth, redoDepth } from "@codemirror/commands";
export { search, searchKeymap, highlightSelectionMatches, SearchQuery, setSearchQuery, getSearchQuery,
  findNext, findPrevious, replaceNext, replaceAll, selectMatches, openSearchPanel, closeSearchPanel,
  gotoLine } from "@codemirror/search";
export { syntaxHighlighting, defaultHighlightStyle, HighlightStyle, indentOnInput, bracketMatching,
  foldGutter, foldKeymap, foldAll, unfoldAll, LanguageDescription, indentUnit, syntaxTree, StreamLanguage } from "@codemirror/language";
export { autocompletion, closeBrackets, closeBracketsKeymap, completionKeymap, acceptCompletion } from "@codemirror/autocomplete";
export { languages } from "@codemirror/language-data";
export { markdown, markdownLanguage } from "@codemirror/lang-markdown";
export { tags } from "@lezer/highlight";
export { showMinimap } from "@replit/codemirror-minimap";
import MarkdownIt from "markdown-it";
export { MarkdownIt };
