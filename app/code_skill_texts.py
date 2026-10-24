"""Built-in MemuCode skills: what each playbook says.

Kept in Python (not in data files) so the frozen ``MemuCode.exe`` and ``MemuAI.exe`` can never ship without them.
Every playbook is written for an agent with file, search and shell tools: when it applies, the steps in order, the
checks that prove the work is done and the shape of the report. Short on purpose — the model loads one only when its
description matches the task, and every line costs context.

The ``description`` is the trigger: one sentence saying what the skill does AND when to use it.
"""
from __future__ import annotations

from typing import Dict, List

SKILLS: List[Dict[str, str]] = [
    {"id": "review", "title": "Code review",
     "description": "Review a diff/branch/files for bugs and risks by severity. Use for reviews and merge-readiness checks."},
    {"id": "test", "title": "Running tests",
     "description": "Find and run the test suite, triage failures to causes. Use when asked to run the tests or explain a red CI run."},
    {"id": "write-tests", "title": "Writing tests",
     "description": "Add focused deterministic tests in the house style. Use for new coverage, regression tests, TDD."},
    {"id": "fix", "title": "Fixing a bug",
     "description": "Reproduce, minimally fix and verify a bug. Use for an error, wrong result or failing test to repair (cause unknown: debug)."},
    {"id": "debug", "title": "Live debugging",
     "description": "Hypothesis-driven investigation with logs and experiments. Use when the cause is unknown (hangs, flaky, servers)."},
    {"id": "refactor", "title": "Refactoring",
     "description": "Restructure code without behavior change in small verified steps. Use for cleanups, renames, extraction."},
    {"id": "explore", "title": "Understanding a codebase",
     "description": "Map an unfamiliar codebase with file:line evidence. Use for how-does-it-work questions spanning many files, not one file."},
    {"id": "commit", "title": "Commit messages",
     "description": "Write a commit message from the staged diff; commit only when asked. Use for commits."},
    {"id": "pr", "title": "Pull requests",
     "description": "Prepare a PR: title, what/why/how-tested description. Use to open or describe a pull request."},
    {"id": "security", "title": "Security review",
     "description": "Audit for injection, traversal, secrets, authz, unsafe exec by severity. Use for security reviews."},
    {"id": "performance", "title": "Performance",
     "description": "Measure, fix the proven hot path, re-measure. Use when something is slow or memory-heavy."},
    {"id": "frontend", "title": "Frontend and UI quality",
     "description": "UI work with design quality, all states, accessibility, visual check. Use for new screens or real UI changes, not a one-line style fix."},
    {"id": "backend", "title": "Backend and APIs",
     "description": "Change APIs/services keeping contracts, validation and errors. Use for endpoints, DB, integrations."},
    {"id": "docs", "title": "Documentation",
     "description": "Write docs/changelogs that match verified behavior. Use when documenting or docs went stale."},
    {"id": "migrate", "title": "Migrations",
     "description": "Reversible schema/data/config migrations with rollback. Use for migrations and breaking upgrades."},
    {"id": "deps", "title": "Dependencies",
     "description": "Add/upgrade/remove dependencies safely with checks. Use for version bumps and lockfile issues."},
    {"id": "release", "title": "Release",
     "description": "Version bump, changelog, build and smoke checks; tag only when asked. Use to cut a release."},
    {"id": "data", "title": "Data analysis",
     "description": "Analyze CSV/JSON/Excel/SQL data with reproducible scripts and sanity checks. Use for questions about a dataset."},
    {"id": "office-files", "title": "Office files and PDF",
     "description": "Read/create/edit .xlsx .docx .pptx .pdf via scripts, keeping formatting. Use when a task reads or writes such a file."},
    {"id": "windows-shell", "title": "Windows shell and encodings",
     "description": "PowerShell/bash/cmd syntax, quoting, Cyrillic paths, CRLF, cp1251/UTF-16. Use when Windows commands fail."},
]

PROMPTS: Dict[str, str] = {
    "review": """# Code review
1. Get the real change: `git diff` (and `git diff --staged`), or the files/branch the user named. Read each changed
   file around the hunks — a diff without context hides most bugs.
2. Understand intent first (commit messages, the task, tests). Review against that intent, not your taste.
3. Look for, in this order: correctness (logic, edge cases: empty/None/zero/huge input, off-by-one, error paths,
   concurrency, resource leaks), security (input reaching shell/SQL/paths/HTML, secrets, authz), data loss and
   compatibility (APIs, schemas, config keys, migrations), tests (is the new behavior and the bug actually covered?),
   then maintainability (naming, duplication, dead code) — style nits last and only if the project cares.
4. Verify claims cheaply where you can: run the tests, grep the callers of a changed function, try the edge case.
5. Report findings grouped by severity — Blocker / Should fix / Nit — each with `path:line`, what goes wrong and in
   which situation, and a concrete fix. Mark guesses as guesses. Say explicitly what you checked and found fine.
   Do not rewrite the code unless asked.""",

    "test": """# Running tests
1. Find the command — do not guess: ProjectInfo, package.json scripts, pyproject/pytest.ini/tox.ini, Makefile,
   CI config (.github/workflows), README. Prefer the narrowest run first (one file / -k pattern), then the full suite.
2. Run it with Bash (run_in_background for long suites, then BashOutput). Never report a result you did not see.
3. For each failure: read the assertion and traceback, open the test and the code under test, classify it —
   real bug, outdated test, environment (missing service, path, encoding, timezone, network), or flaky
   (re-run once to confirm).
4. Fix only what the task asks; if you change a test, explain why the old expectation was wrong.
5. Report: command, totals (passed/failed/skipped), each failure with its cause in one line, what you fixed, what
   remains.""",

    "write-tests": """# Writing tests
1. Learn the house style before writing: framework, file naming, fixtures/factories, how mocks are done, where
   tests live. Copy the nearest existing test's structure.
2. Decide what behavior to pin: the public contract, each branch that matters, boundaries (empty, one, many,
   max, invalid, unicode/paths with spaces), error cases. For a bug: first a test that fails for the bug's reason.
3. One behavior per test, a name that states it (`test_rejects_expired_token`), arrange-act-assert, no logic in
   tests, no sleeps (inject clocks, use events), no network or real home directories (temp dirs, fakes).
4. Mock at the boundary (HTTP, clock, filesystem roots, subprocess), never the unit under test.
5. Run the new tests and watch them fail without the fix / pass with it; run the surrounding suite. Report which
   behaviors are now covered and any gaps left on purpose.""",

    "fix": """# Fixing a bug
1. Reproduce: get the exact error, input and steps; write the smallest script or failing test that shows it.
   No reproduction yet? Gather evidence (logs, stack trace, git log/blame of the area) until you have one.
2. Locate the cause, not the symptom: follow the data from input to failure; check the last change to that code.
   State the mechanism in one or two sentences before editing ("X is None when Y because Z").
3. Fix minimally at the cause. Search for the same pattern elsewhere (Grep) and fix siblings if they share the bug.
   No unrelated refactors.
4. Verify: the reproduction now passes, add/keep it as a regression test, run the related suite.
5. Report: cause, fix (`path:line`), proof (commands and results), any risk or follow-up.""",

    "debug": """# Live debugging
1. Write down the symptom precisely (what, when, how often, since when, what changed) and what "fixed" will look
   like.
2. Collect evidence before theorizing: full error text, logs, versions, environment, `git log` around the time it
   started. Make the failure cheap to trigger.
3. List hypotheses ranked by likelihood × cost to test. Test one at a time with an experiment whose outcome you
   predict beforehand: add logging, bisect (`git bisect`, halving inputs/config), isolate components, compare a
   working and a broken setup.
4. Keep a short log of what you tried and what it showed; discard hypotheses the evidence kills.
5. For servers/watchers use Bash run_in_background and read with BashOutput; stop them with KillShell when done.
   Remove temporary instrumentation afterwards.
6. When found: explain the root cause with the evidence, then fix (see the fix skill) and verify.""",

    "refactor": """# Refactoring
1. Make sure behavior is pinned: find the tests covering the code; if there are none, add characterization tests
   first (they assert what the code does today).
2. Plan small steps that each keep the code working: rename, extract function, move, inline, replace duplication.
   Find every usage before renaming or moving (Grep for the name, imports, strings, configs, docs).
3. After each step run the relevant tests. Keep public APIs, file formats and config keys stable unless the task
   says otherwise; if they change, provide a compatibility path.
4. Match the existing style; do not mix in feature changes or reformat untouched code.
5. Report the steps, the before/after structure in a few lines and the test results.""",

    "explore": """# Understanding a codebase
1. Orient: README, top-level layout (LS), manifests (package.json, pyproject, go.mod…), entry points (main,
   app, server, CLI), CI config, docs for AI agents (AGENTS.md, CLAUDE.md, MEMUCODE.md).
2. Follow the request path end to end for the question asked: entry → routing → business logic → storage/IO.
   Use Grep for names and strings, Outline/Symbols for structure; read the key functions, not whole folders.
3. Note conventions: error handling, config, logging, tests, naming, dependency injection.
4. Answer the user's actual question with a short map: components and their responsibility, the flow with
   `path:line` references, gotchas. Distinguish what you read from what you infer. Do not change files.""",

    "commit": """# Commit messages
1. Inspect `git status`, `git diff --staged` (and unstaged changes). If nothing is staged, say so and ask which
   files to include, unless the user said "commit everything".
2. Check for things that must not be committed: secrets, .env files, large binaries, debug prints, unrelated
   changes. Point them out.
3. Write the message in the repository's style (look at `git log --oneline -15`); default to Conventional Commits:
   `type(scope): imperative summary` ≤ 72 chars (feat, fix, refactor, perf, test, docs, build, ci, chore), a blank
   line, then a body explaining why and anything non-obvious; `BREAKING CHANGE:` footer when relevant.
4. Commit only if the user asked to commit (pass the message with a here-doc or -F file to keep newlines and
   quotes intact). Never amend, rebase or push unless explicitly asked.""",

    "pr": """# Pull requests
1. Compare the branch with its base (`git log base..HEAD`, `git diff base...HEAD`). Make sure tests and linters
   pass first; mention anything you could not run.
2. Suggest cleaning history only if the user wants it (squash fixups) — never rewrite pushed history on your own.
3. Title: imperative, specific, ≤ 70 chars. Description: Summary (what and why), Changes (bullets by area),
   How tested (commands, manual steps), Risks/rollout (migrations, flags, breaking changes), screenshots for UI.
4. Create or push the PR only when asked (gh CLI if available); otherwise output the text ready to paste.""",

    "security": """# Security review
1. Map the attack surface of the code in scope: inputs (HTTP, CLI args, files, env, IPC, model output), where
   they flow, privileges, secrets.
2. Check each flow for: command/SQL/template/HTML injection, path traversal and symlink escapes, SSRF (private
   addresses, redirects), unsafe deserialization (pickle, yaml.load, eval), authn/authz gaps and IDOR, CSRF,
   secrets in code/logs/errors, weak crypto or randomness, race conditions (TOCTOU), unbounded resources (DoS),
   dependency vulnerabilities.
3. Prove what you can: show the exact input that reaches the sink, or why it cannot. Never run exploits against
   systems you are not authorised to test.
4. Report by severity (Critical/High/Medium/Low) with `path:line`, scenario, impact and a concrete fix; list the
   areas you checked and found sound.""",

    "performance": """# Performance
1. Define the metric and the target (latency p50/p95, throughput, memory, startup time) and how to measure it.
2. Measure the baseline with a repeatable benchmark or profiler (cProfile/py-spy, Chrome DevTools, `time`,
   EXPLAIN for queries). Do not optimize without a measurement pointing at the hot spot.
3. Fix the biggest proven cost first: algorithmic complexity, N+1 queries, repeated IO, missing caching or
   indexes, needless work on the hot path, blocking calls in async code, oversized payloads.
4. Re-measure with the same method; keep the change only if it helps, and keep behavior identical (run tests).
5. Report before/after numbers, what changed and the trade-offs.""",

    "frontend": """# Frontend and UI quality
1. Read the existing design system first: tokens/CSS variables, components, spacing scale, typography, themes
   (light/dark), i18n mechanism. Reuse; do not invent a parallel style.
2. Build with intent: a clear visual hierarchy (one primary action), consistent spacing and alignment, readable
   type sizes and line lengths, sufficient contrast (WCAG AA), restrained color and motion.
3. Cover every state: loading, empty, error, disabled, long text/overflow, many items, narrow and wide screens,
   hover/focus/active. Respect prefers-reduced-motion.
4. Accessibility: semantic elements (button, label, nav), keyboard operation and visible focus, alt text, aria only
   where semantics are missing.
5. Verify visually when you can (screenshot, browser) and check the console for errors. Report what you changed and
   which states you checked.""",

    "backend": """# Backend and APIs
1. Read the existing handler/service/repository pattern, validation, error format and tests before writing.
2. Preserve contracts: routes, request/response shapes, status codes, error formats, config keys. Additive changes
   over breaking ones; version or migrate when breaking is unavoidable.
3. Validate input at the boundary, handle errors explicitly (no bare excepts that swallow), keep transactions
   consistent, make retries idempotent, set timeouts on outbound calls, never log secrets.
4. Mind concurrency and resources: connection pools, async vs blocking IO, locks, pagination and limits.
5. Add or update focused tests (happy path, validation error, not found/permission, edge cases) and run them.""",

    "docs": """# Documentation
1. Verify behavior before writing: read the code, run the command, check defaults and flags. Never document what you
   did not confirm.
2. Write for the reader's task: what it is, quick start (copy-pasteable commands), common tasks, configuration
   reference, troubleshooting. Examples beat prose.
3. Keep the project's tone, language and format; update the table of contents and links; delete stale statements
   instead of piling caveats on them.
4. For changelogs: user-visible changes grouped (Added/Changed/Fixed/Removed), each with the why.
5. Report which files changed and anything you could not verify.""",

    "migrate": """# Migrations
1. Inventory what depends on the old shape: code paths, stored data, other services, backups, config files.
2. Prefer expand → migrate → contract: add the new shape alongside the old, backfill, switch readers and writers,
   remove the old only when nothing uses it.
3. Make it reversible: a down/rollback step or a backup taken before the change; batch large data moves; keep it
   idempotent so a re-run is safe.
4. Test on a copy: old data in, new code reads it, rollback works. Never run a migration against production or
   user data without explicit approval.
5. Report the plan, the commands, how to roll back and what was verified.""",

    "deps": """# Dependencies
1. Find how the project pins dependencies (lockfile, ranges, constraints) and its package manager — use that tool,
   not another one.
2. Before an upgrade read the changelog/release notes for breaking changes between the two versions. Upgrade one
   package (or one coherent group) at a time.
3. Install, then run build, type checks and tests. Fix breakages or roll back that package.
4. For a new dependency: prefer maintained, small, widely used packages; check the license; avoid adding a package
   for a few lines of code.
5. Report versions before → after, notable changes and the check results. Do not install anything system-wide.""",

    "release": """# Release
1. Confirm what is being released and the versioning scheme; find where the version lives (all places).
2. Generate the changelog from history since the last tag (`git log <tag>..HEAD`), written for users.
3. Bump the version, build the artifacts with the project's own build command, run tests and a smoke check of the
   built artifact (install/run it).
4. Tag, push and publish only when the user explicitly asks; show the commands first.
5. Report version, artifacts with sizes/paths, check results and anything skipped.""",

    "data": """# Data analysis
1. Inspect before analyzing: file size, encoding (UTF-8 / cp1251 / BOM), delimiter, header row, types, missing
   values, duplicates, units, date formats. Look at a sample of rows.
2. Work in a script (Python + pandas, or SQL), not by eye; keep it in the project or a temp file so the result is
   reproducible. For big files read in chunks or select columns.
3. Clean explicitly and say what you dropped or changed and how many rows it affected.
4. Answer the question with the simplest correct method; sanity-check totals and orders of magnitude against the
   raw data; beware of averages over skewed data and of mixing units or currencies.
5. Charts: matplotlib/plotly saved to a file, labeled axes with units, a title that states the finding.
6. Report the answer first, then the method, caveats and the script path.""",

    "office-files": """# Office files and PDF
1. Never edit binary formats as text. Use a script with the right library — check what is installed first
   (`python -c "import openpyxl, docx, pptx, pypdf"`); install project-locally only with the user's consent.
   - .xlsx: openpyxl (keeps formatting, formulas as text; `data_only=True` reads cached values), pandas for analysis.
   - .docx: python-docx (paragraphs, runs, tables, styles). .pptx: python-pptx.
   - .pdf: pypdf to read/merge/split; text extraction can be imperfect — say so; reportlab to create.
   - .csv for Excel on Russian Windows: `;` delimiter and UTF-8 with BOM (`utf-8-sig`) so Cyrillic opens correctly.
2. Write to a new file or keep a backup unless the user asked to overwrite; never break existing styles, formulas,
   merged cells or page setup you were not asked to change.
3. Verify by reading the result back with the library (and, for layout, by converting to an image if possible).
4. Report the output path and what changed.""",

    "windows-shell": """# Windows shell and encodings
1. Know which shell runs your commands (see the environment block). Git Bash: POSIX syntax, paths as C:/dir or
   /c/dir. Windows PowerShell 5.1: no `&&`/`||` (use `;` and `if ($?) { … }`), `$env:VAR`, `Get-ChildItem`,
   `Remove-Item -Recurse -Force`, quote with single quotes for literals. cmd: `%VAR%`, `&&` works, `^` escapes.
2. Quote every path: spaces and Cyrillic are normal on Windows ("C:/Users/Иван/Мои проекты"). Prefer forward
   slashes; inside JSON escape backslashes.
3. Encodings: Python reads files with the locale code page by default — always pass `encoding="utf-8"` (or detect
   cp1251 / UTF-16 BOM). PowerShell 5.1 `>` and `Out-File` write UTF-16 LE: use `Set-Content -Encoding utf8` or
   write files with the Write tool. Console output of old tools may be cp866.
4. Line endings: keep each file's existing CRLF/LF; do not convert whole files. `git config core.autocrlf` may
   rewrite them on checkout.
5. Files in use cannot be deleted or replaced (running exe, open log): stop the process first. Long paths over 260
   characters can fail in older tools. Antivirus may lock fresh build outputs for a moment — retry once.
6. Never use `rm -rf /`-style commands, `format`, `diskpart`, registry or system-wide changes without explicit
   request.""",
}
