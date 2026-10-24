"""Find prompt-like string constants (>= MIN chars) in python and JS sources; print file:line, chars, ~tokens, head.

Not part of the normal test suite. Usage: python3 tests/prompt_eval/catalog.py [files...]
"""
import ast, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
MIN = 250
try:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")
    def toks(s): return len(_enc.encode(s))
except Exception:  # pragma: no cover
    def toks(s): return len(s) // 4
WORDS = re.compile(r"\b(you|your|must|never|always|answer|user|return|only|JSON|reply)\b", re.I)

def py(path):
    try:
        tree = ast.parse(path.read_text("utf-8"))
    except Exception:
        return
    seen = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            s = "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value
        else:
            continue
        if len(s) >= MIN and len(WORDS.findall(s)) >= 3 and node.lineno not in seen:
            seen.add(node.lineno)
            yield node.lineno, s

JS_STR = re.compile(r"`(?:[^`\\]|\\.)*`|\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'", re.S)
def js(path):
    text = path.read_text("utf-8", "replace")
    for m in JS_STR.finditer(text):
        s = m.group(0)[1:-1]
        if len(s) >= MIN and len(WORDS.findall(s)) >= 3 and not s.lstrip().startswith("<svg"):
            yield text.count("\n", 0, m.start()) + 1, s

def main(argv):
    files = [Path(a) for a in argv] or sorted([*ROOT.glob("app/**/*.py"), *ROOT.glob("app/pgstatic/**/*.js")])
    total = 0
    for f in files:
        if "vendor" in f.parts or "test" in f.name:
            continue
        rows = list((py if f.suffix == ".py" else js)(f))
        for line, s in rows:
            t = toks(s); total += t
            head = re.sub(r"\s+", " ", s)[:90]
            print(f"{f.relative_to(ROOT)}:{line}\t{len(s)}c\t{t}t\t{head}")
    print("TOTAL tokens", total)

if __name__ == "__main__":
    main(sys.argv[1:])
