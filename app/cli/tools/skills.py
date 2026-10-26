"""The SkillManage tool: the agent can list, create, change, delete, disable and enable skills.

Without it a model asked to "delete that skill" has nothing to go on — it does not know where skills live or
that a built-in one cannot be removed. The tool is the one supported way; the user does the same with ``/skills``.
Creating or changing a skill, and any delete, goes through the usual permission prompt.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from ... import work_tools
from .. import skillkit
from ..i18n import tr
from .base import Prepared, Tool, ToolError, ToolResult, arg

class SkillManageTool(Tool):
    name = "SkillManage"
    aliases = ("skill_manage", "manage_skill", "manage_skills", "skills")
    kind = "edit"
    plan_safe = False
    short = ("list, create, update, delete, disable or enable skills "
             "{action, name?, description?, content?, scope?: project|personal}")
    description = (
        "Manages MemuCode skills (playbooks loaded for a kind of task); the only supported way to change them — never "
        "Bash or Write. list: every skill with source and file. create: name (slug), description (one sentence: what "
        "it does and WHEN to use it), content (Markdown instructions; the header is added), scope project|personal. "
        "update: new description and/or content of a project/personal skill. delete: moves a project/personal skill "
        "to a restorable trash; built-ins cannot be deleted — disable/enable them instead.")
    parameters = {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["list", "create", "update", "delete", "disable", "enable"]},
        "name": {"type": "string", "description": "Skill id, a short slug such as release-notes"},
        "description": {"type": "string", "description": "One sentence: what the skill does and when to use it"},
        "content": {"type": "string", "description": "The instructions (Markdown). The header is written for you"},
        "scope": {"type": "string", "enum": ["project", "personal"],
                  "description": "Where a new skill lives (default: project when the folder is trusted, else personal)"}}}
    required = ("action",)

    def display(self, args: Dict[str, Any]) -> str:
        action = str(args.get("action") or "")
        name = str(args.get("name") or "")
        return f"{action} {name}".strip()

    def permission_key(self, runtime, args):
        return "skill:" + str(args.get("name") or args.get("action") or "")

    async def prepare(self, ctx, args):
        runtime = ctx.runtime
        action = str(arg(args, "action", default="list")).strip().lower()
        if action not in ("list", "create", "update", "delete", "disable", "enable"):
            raise ToolError("action must be list, create, update, delete, disable or enable.")
        if action == "list":
            return Prepared(key="skills", readonly=True, data={"action": action})
        slug = skillkit.slugify(str(arg(args, "name", "id", "skill", default="")))
        problem = skillkit.name_problem(slug)
        if problem:
            raise ToolError(problem)
        existing = runtime.skills.get(slug, include_disabled=True)
        if action in ("disable", "enable"):
            if existing is None:
                raise ToolError(f"No skill named {slug!r}. Use action=list to see them.")
            return Prepared(key="skill:" + slug, title=tr("Изменить настройки скиллов", "Change the skills settings"),
                            preview=f"{action} {slug}", outside=True, data={"action": action, "slug": slug})
        if action == "delete":
            if existing is None:
                raise ToolError(f"No skill named {slug!r}. Use action=list to see them.")
            if existing.get("builtin") or not existing.get("path"):
                raise ToolError(f"{slug!r} is a built-in skill and cannot be deleted. Use action=disable to switch it off.")
            return Prepared(key="skill:" + slug, title=tr("Удалить скилл", "Delete skill"),
                            preview=f"{slug}\n{existing.get('path')}\n" + tr("уйдёт в корзину и его можно вернуть",
                                                                            "goes to the trash and can be restored"),
                            outside=True, data={"action": action, "slug": slug, "path": existing["path"]})
        scope = str(args.get("scope") or "").strip().lower() or ("project" if runtime.trusted() else "personal")
        if scope not in skillkit.SCOPES:
            raise ToolError("scope must be 'project' or 'personal'.")
        description = " ".join(str(args.get("description") or "").split())
        content = args.get("content")
        content = content if isinstance(content, str) else ""
        target = skillkit.skill_dir(runtime.root, scope, slug) / "SKILL.md"
        before = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
        if action == "create":
            if existing is not None and existing.get("path") and Path(existing["path"]).resolve() == target.resolve():
                raise ToolError(f"The skill {slug!r} already exists at {target}. Use action=update to change it.")
            if not content.strip():
                raise ToolError("content (the instructions) is required to create a skill.")
        else:
            if not before:
                owner = existing.get("path") if existing else ""
                raise ToolError(f"There is no {scope} skill {slug!r}" + (f" (it is at {owner}; pass that scope)" if owner else "")
                                + ". Built-in skills cannot be edited — create one with the same name instead.")
        text = self._compose(slug, description, content, before)
        issues = skillkit.check_text(text)
        if issues:
            raise ToolError("The skill is not valid: " + "; ".join(issues))
        diff = work_tools.unified_diff(before, text, f"{slug}/SKILL.md", limit=200) if before else ""
        return Prepared(key=str(target), preview=diff or text[:2500], diff=diff,
                        title=tr("Создать скилл", "Create skill") if action == "create" else tr("Изменить скилл", "Change skill"),
                        outside=scope == "personal",
                        data={"action": action, "slug": slug, "scope": scope, "text": text, "target": target})

    @staticmethod
    def _compose(slug: str, description: str, content: str, before: str) -> str:
        """The whole SKILL.md: header from the given description (or the old one) + the new or the old body."""
        content = content.replace("\r\n", "\n")
        meta_new, body_new = skillkit.parse(content)
        meta_old, body_old = skillkit.parse(before) if before else ({}, "")
        if meta_new and not description:
            description = meta_new.get("description", "")
        description = description or meta_old.get("description", "")
        body = (body_new if meta_new else content).strip() if content.strip() else body_old.strip()
        header = ["---", f"name: {slug}", f"description: {description}"]
        for key, value in (meta_old if not meta_new else meta_new).items():
            if key not in ("name", "description", "title") and value:
                header.append(f"{key}: {value}")
        return "\n".join(header) + "\n---\n\n" + body + "\n"

    async def run(self, ctx, args, prepared):
        runtime = ctx.runtime
        data = prepared.data
        action = data["action"]
        if action == "list":
            rows = runtime.skills.catalogue(refresh=True, include_disabled=True)
            lines = [f"- {row['id']} [{row.get('source')}{'' if row.get('enabled', True) else ', disabled'}]: "
                     f"{row.get('description') or row.get('title') or ''}" + (f" ({row['path']})" if row.get("path") else "")
                     for row in rows]
            where = skillkit.location_help(runtime.root)
            return ToolResult(ok=True, output="\n".join(lines) + f"\n\nLocations — {where}",
                              summary=tr(f"Скиллов: {len(rows)}", f"{len(rows)} skills"))
        slug = data["slug"]
        if action in ("disable", "enable"):
            skillkit.set_disabled(runtime.settings, slug, action == "disable")
            self._reload(runtime)
            return ToolResult(ok=True, output=f"Skill {slug!r} is now {'disabled' if action == 'disable' else 'enabled'}.",
                              summary=f"{action} {slug}")
        if action == "delete":
            moved = skillkit.delete_skill(runtime.root, Path(data["path"]))
            self._reload(runtime)
            return ToolResult(ok=True, output=f"Skill {slug!r} was removed (kept in the trash: {moved}). "
                                              "The user can bring it back with /skills → restore.",
                              summary=tr(f"Скилл «{slug}» удалён", f"Deleted skill {slug}"))
        try:
            written = skillkit.write_skill(runtime.root, data["scope"], slug, data["text"], overwrite=action == "update")
        except (OSError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        self._reload(runtime)
        verb = "Created" if action == "create" else "Updated"
        return ToolResult(ok=True, output=f"{verb} skill {slug!r} at {written}. It is available now — load it with the "
                                          f"Skill tool, or the user runs /{slug} <task>.",
                          summary=tr(f"Скилл «{slug}»: {'создан' if action == 'create' else 'обновлён'}",
                                     f"{verb} skill {slug}"), diff=prepared.diff)

    @staticmethod
    def _reload(runtime) -> None:
        runtime.invalidate_prompt(memory=True)
        runtime.skills.catalogue(refresh=True, include_disabled=True)

def register(registry) -> None:
    registry.add(SkillManageTool())
