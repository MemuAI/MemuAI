// MemuNote (Round 33, item #9) — code autocomplete.
//
// CodeMirror's completion popup (Ctrl+Space or while typing, Enter/Tab to accept, Esc to close) fed by:
//   1. the language package's own sources where it has them (JavaScript/TypeScript scope + snippets, Python
//      builtins + local names, CSS properties, HTML tags/attributes, SQL keywords…);
//   2. keywords, builtins and snippets for the languages that have no source of their own (Go, Rust, Java, C/C++,
//      C#, PHP, Ruby, Lua, PowerShell, Bash, Batch, Kotlin, Swift, Dart, YAML…) and common globals for JS/Python;
//   3. identifiers already used in the file (any language), ranked below exact language words.
// Nothing is completed inside comments and strings unless asked explicitly (Ctrl+Space).
// Brackets/quotes auto-close and indentation are the editor's (closeBrackets, indentOnInput); the optional AI
// inline suggestion (ghost text, Tab) is ai.js requestGhost — see the "AI suggestions" toggle in the status bar.
import { CM } from "./editor.js";

const { EditorState, syntaxTree } = CM;

const W = (s) => s.split(/\s+/).filter(Boolean);
// [keywords, builtins/types, snippets {label, detail, body}] — body: "${}" marks the caret, "\n" lines keep indent
const LANG = {
  python: [W("False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case self"),
    W("print len range enumerate zip map filter sorted reversed sum min max abs round open input int float str bool list dict set tuple type isinstance issubclass hasattr getattr setattr super object Exception ValueError TypeError KeyError IndexError RuntimeError FileNotFoundError StopIteration any all iter next repr format id hash dir vars help divmod pow chr ord bin hex oct bytes bytearray frozenset property staticmethod classmethod __name__ __main__ __init__ __file__"),
    [{ label: "ifmain", detail: "if __name__ == \"__main__\"", body: "if __name__ == \"__main__\":\n\t${}" },
     { label: "def", detail: "def name(…):", body: "def ${}():\n\tpass" }, { label: "class", detail: "class Name:", body: "class ${}:\n\tdef __init__(self):\n\t\tpass" },
     { label: "for", detail: "for … in …:", body: "for ${} in :\n\tpass" }, { label: "with open", detail: "with open(…) as f:", body: "with open(${}, encoding=\"utf-8\") as f:\n\t" },
     { label: "try", detail: "try / except", body: "try:\n\t${}\nexcept Exception as exc:\n\traise" }]],
  javascript: [W("break case catch class const continue debugger default delete do else export extends finally for function if import in instanceof let new return super switch this throw try typeof var void while with yield async await of static get set null undefined true false"),
    W("console log warn error info document window globalThis Math JSON Promise Array Object String Number Boolean Map Set WeakMap Symbol Date RegExp Error fetch setTimeout clearTimeout setInterval clearInterval requestAnimationFrame localStorage sessionStorage parseInt parseFloat isNaN encodeURIComponent decodeURIComponent structuredClone queueMicrotask require module exports process Buffer __dirname __filename addEventListener querySelector querySelectorAll getElementById createElement textContent innerHTML classList appendChild"),
    [{ label: "log", detail: "console.log(…)", body: "console.log(${});" }, { label: "fn", detail: "function name() {}", body: "function ${}() {\n\t\n}" },
     { label: "arrow", detail: "() => {}", body: "(${}) => {\n\t\n}" }, { label: "fori", detail: "for (let i = 0; …)", body: "for (let i = 0; i < ${}; i++) {\n\t\n}" },
     { label: "forof", detail: "for (const x of …)", body: "for (const item of ${}) {\n\t\n}" }, { label: "trycatch", detail: "try { } catch", body: "try {\n\t${}\n} catch (err) {\n\tconsole.error(err);\n}" }]],
  go: [W("break case chan const continue default defer else fallthrough for func go goto if import interface map package range return select struct switch type var nil true false iota"),
    W("append cap close complex copy delete imag len make new panic print println real recover bool byte rune string error int int8 int16 int32 int64 uint uint8 uint16 uint32 uint64 float32 float64 any fmt Println Printf Sprintf Errorf os strings strconv time context"),
    [{ label: "main", detail: "package main …", body: "package main\n\nimport \"fmt\"\n\nfunc main() {\n\t${}\n}" }, { label: "iferr", detail: "if err != nil", body: "if err != nil {\n\treturn ${}err\n}" },
     { label: "func", detail: "func name() {}", body: "func ${}() {\n\t\n}" }, { label: "forr", detail: "for i, v := range …", body: "for i, v := range ${} {\n\t\n}" }]],
  rust: [W("as async await break const continue crate dyn else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while"),
    W("println print format vec Vec String Option Some None Result Ok Err Box Rc Arc HashMap HashSet BTreeMap i8 i16 i32 i64 i128 isize u8 u16 u32 u64 u128 usize f32 f64 bool char str clone unwrap expect iter collect map filter into to_string len push"),
    [{ label: "main", detail: "fn main() {}", body: "fn main() {\n\t${}\n}" }, { label: "fn", detail: "fn name() {}", body: "fn ${}() {\n\t\n}" }, { label: "match", detail: "match … {}", body: "match ${} {\n\t_ => {}\n}" }]],
  java: [W("abstract assert boolean break byte case catch char class const continue default do double else enum extends final finally float for goto if implements import instanceof int interface long native new package private protected public return short static strictfp super switch synchronized this throw throws transient try void volatile while var record true false null"),
    W("String System out println printf Integer Long Double Boolean List ArrayList Map HashMap Set HashSet Arrays Collections Optional Stream Math Object Exception RuntimeException Override"),
    [{ label: "main", detail: "public static void main", body: "public static void main(String[] args) {\n\t${}\n}" }, { label: "sout", detail: "System.out.println", body: "System.out.println(${});" }]],
  c: [W("auto break case char const continue default do double else enum extern float for goto if inline int long register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while NULL true false include define ifdef ifndef endif"),
    W("printf scanf malloc calloc realloc free memcpy memset strlen strcmp strcpy fopen fclose fprintf fgets puts size_t uint8_t uint32_t int64_t bool stdio stdlib string"),
    [{ label: "main", detail: "int main(void)", body: "int main(void) {\n\t${}\n\treturn 0;\n}" }, { label: "for", detail: "for (int i = 0; …)", body: "for (int i = 0; i < ${}; i++) {\n\t\n}" }]],
  cpp: [W("alignas auto bool break case catch char class const constexpr continue decltype default delete do double else enum explicit export extern false float for friend goto if inline int long mutable namespace new noexcept nullptr operator private protected public return short signed sizeof static struct switch template this throw true try typedef typename union unsigned using virtual void volatile while include"),
    W("std cout cin endl string vector map unordered_map set pair make_pair unique_ptr shared_ptr make_unique size push_back emplace_back begin end sort find printf"),
    [{ label: "main", detail: "int main()", body: "int main() {\n\t${}\n\treturn 0;\n}" }, { label: "forr", detail: "for (auto& x : …)", body: "for (auto& item : ${}) {\n\t\n}" }]],
  csharp: [W("abstract as base bool break byte case catch char checked class const continue decimal default delegate do double else enum event explicit extern false finally fixed float for foreach goto if implicit in int interface internal is lock long namespace new null object operator out override params private protected public readonly ref return sbyte sealed short sizeof stackalloc static string struct switch this throw true try typeof uint ulong unchecked unsafe ushort using var virtual void volatile while async await record"),
    W("Console WriteLine ReadLine Math List Dictionary HashSet Task string int bool DateTime Exception Linq Select Where ToList"),
    [{ label: "cw", detail: "Console.WriteLine", body: "Console.WriteLine(${});" }]],
  php: [W("abstract and array as break callable case catch class clone const continue declare default do echo else elseif empty enddeclare endfor endforeach endif endswitch endwhile extends final finally fn for foreach function global goto if implements include include_once instanceof insteadof interface isset list match namespace new or print private protected public readonly require require_once return static switch throw trait try unset use var while xor yield true false null"),
    W("strlen str_replace explode implode array_map array_filter count in_array json_encode json_decode var_dump print_r isset empty"), []],
  ruby: [W("BEGIN END alias and begin break case class def defined? do else elsif end ensure false for if in module next nil not or redo rescue retry return self super then true undef unless until when while yield require attr_accessor"),
    W("puts print p gets each map select reject reduce times upto to_s to_i length size push include?"), []],
  lua: [W("and break do else elseif end false for function goto if in local nil not or repeat return then true until while"),
    W("print pairs ipairs tostring tonumber type table insert remove concat string format sub gsub find math floor random require pcall error setmetatable"), []],
  powershell: [W("begin break catch class continue data define do dynamicparam else elseif end exit filter finally for foreach from function if in param process return switch throw trap try until using var while"),
    W("Write-Host Write-Output Write-Error Get-ChildItem Get-Content Set-Content Get-Item Test-Path New-Item Remove-Item Copy-Item Move-Item Get-Process Start-Process Stop-Process Invoke-WebRequest Invoke-RestMethod ForEach-Object Where-Object Select-Object Sort-Object Measure-Object ConvertTo-Json ConvertFrom-Json Read-Host Join-Path Split-Path $true $false $null $PSScriptRoot $env"), []],
  shell: [W("if then else elif fi case esac for while until do done in function select time return exit local export readonly declare unset shift break continue"),
    W("echo printf read cd ls cat grep sed awk find xargs sort uniq head tail wc cut tr mkdir rm cp mv chmod chown curl wget test source set trap"), []],
  batch: [W("echo set if else goto call exit setlocal endlocal for do in not exist defined errorlevel pause rem shift pushd popd start choice title cls"), W("%~dp0 %errorlevel% enabledelayedexpansion nul"), []],
  kotlin: [W("as break class continue do else false for fun if in interface is null object package return super this throw true try typealias typeof val var when while by catch constructor init lateinit override private protected public internal data sealed suspend"), W("println print listOf mutableListOf mapOf setOf String Int Long Double Boolean Unit Any"), []],
  swift: [W("associatedtype class deinit enum extension func import init inout internal let open operator private protocol public static struct subscript typealias var break case continue default defer do else fallthrough for guard if in repeat return switch where while as catch false is nil rethrows super self Self throw throws true try"), W("print String Int Double Bool Array Dictionary Optional"), []],
  dart: [W("abstract as assert async await break case catch class const continue default do dynamic else enum export extends extension external factory false final finally for get if implements import in is late library mixin new null on operator part required rethrow return set show static super switch this throw true try typedef var void while with yield"), W("print String int double bool List Map Set Future Stream"), []],
  yaml: [W("true false null yes no on off"), [], []],
  sql: [[], [], []],
};
const ALIAS = { Python: "python", JavaScript: "javascript", TypeScript: "javascript", JSX: "javascript", TSX: "javascript", Go: "go", Rust: "rust",
  Java: "java", C: "c", "C++": "cpp", "C#": "csharp", PHP: "php", Ruby: "ruby", Lua: "lua", PowerShell: "powershell", Shell: "shell",
  Kotlin: "kotlin", Swift: "swift", Dart: "dart", YAML: "yaml", SQL: "sql" };
export function langKey(langName, filename = "") {
  if (/\.(bat|cmd)$/i.test(filename)) return "batch";
  if (/\.ps1$/i.test(filename)) return "powershell";
  return ALIAS[langName] || "";
}

function inCommentOrString(state, pos) {
  const node = syntaxTree(state).resolveInner(pos, -1);
  for (let n = node; n; n = n.parent) if (/comment|string/i.test(n.name)) return true;
  return false;
}

function snippetApply(body) {
  return (view, completion, from, to) => {
    const { state } = view;
    const line = state.doc.lineAt(from);
    const indent = /^\s*/.exec(line.text)[0];
    const unit = state.facet(CM.indentUnit) || "    ";
    let text = body.replace(/\t/g, unit).split("\n").map((l, i) => (i ? indent + l : l)).join("\n");
    let caret = text.indexOf("${}");
    text = text.replace("${}", "");
    if (caret < 0) caret = text.length;
    view.dispatch({ changes: { from, to, insert: text }, selection: { anchor: from + caret }, userEvent: "input.complete", scrollIntoView: true });
  };
}

const wordCache = new WeakMap();
function bufferWords(state, exclude) {
  const doc = state.doc;
  let entry = wordCache.get(doc);
  if (!entry) {
    const set = new Set();
    if (doc.length < 3 * 1048576) {
      const re = /[A-Za-z_$\u00C0-\u024F\u0400-\u04FF][\w$\u00C0-\u024F\u0400-\u04FF]{2,}/g;
      for (const text of doc.iter()) { if (typeof text === "string") for (const m of text.matchAll(re)) { set.add(m[0]); if (set.size > 6000) break; } }
    }
    entry = set; wordCache.set(doc, entry);
  }
  return [...entry].filter((w) => w !== exclude);
}

/** The completion source for one tab: (langKey) → CompletionSource */
export function codeCompletionSource(getKey) {
  return (ctx) => {
    const m = ctx.matchBefore(/[A-Za-z_$@%~][\w$\-]*/) || (ctx.explicit ? { from: ctx.pos, to: ctx.pos, text: "" } : null);
    if (!m) return null;
    // no null for a 1-letter prefix: a source that answered null is not asked again while another source's list
    // stays valid, so the snippets/keywords would never appear after the second letter
    if (!ctx.explicit && (m.text.length < 1 || inCommentOrString(ctx.state, ctx.pos))) return null;
    const key = getKey();
    const [kw, builtins, snippets] = LANG[key] || [[], [], []];
    const options = [];
    const seen = new Set();
    for (const s of snippets) { options.push({ label: s.label, detail: s.detail, type: "text", boost: 3, apply: snippetApply(s.body)}); }
    for (const k of kw) if (!seen.has(k)) { seen.add(k); options.push({ label: k, type: "keyword", boost: 2 }); }
    for (const b of builtins) if (!seen.has(b)) { seen.add(b); options.push({ label: b, type: /^[A-Z]/.test(b) ? "class" : "function", boost: 1 }); }
    for (const w of bufferWords(ctx.state, m.text)) if (!seen.has(w)) { seen.add(w); options.push({ label: w, type: "variable", boost: -1 }); }
    return { from: m.from, options, validFor: /^[\w$\-]*$/ };
  };
}

/** Extensions for a code tab (empty for prose). */
export function codeComplete(getKey, enabled) {
  if (!enabled) return [];
  return [
    CM.autocompletion({ activateOnTyping: true, maxRenderedOptions: 80, defaultKeymap: true, closeOnBlur: true, icons: true }),
    // ONE source function per editor: CodeMirror tells sources apart by identity (a new function on every lookup
    // made it drop this source's results)
    ((src) => EditorState.languageData.of(() => [{ autocomplete: src }]))(codeCompletionSource(getKey)),
  ];
}
export const _test = { LANG, langKey };
