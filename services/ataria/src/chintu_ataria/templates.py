"""Fill Ataria's prompt and skill templates from the owner's profile at run time.

Templates in the repository hold no personal information; every personal value comes from
`ataria.json` (profile.py) when a run starts. Syntax:

  {{ owner.name }}                 a profile value (dotted path); a missing one stops the render
  {{ owner.role? }}                optional: renders empty when missing
  {{ work.clients | join }}        a list joined with ", "  (also: | names, | bullets, | json)
  {{#if family}} ... {{else}} ... {{/if}}   a section kept only when the value is present and non-empty
  {{include skills/work.md}}       another template, relative to the templates folder (3 levels deep)
  {{ATARIA_ROOT}}                  an upper-case runtime value (paths, dates) from the caller, never the profile

A render that needs a value the profile does not have raises TemplateError naming every missing
field, so a run never starts with a half-filled prompt.
"""

import json
import re
from pathlib import Path

INCLUDE = re.compile(r"\{\{include ([\w./-]+)\}\}")
IF_BLOCK = re.compile(r"\{\{#if ([a-z_][a-z0-9_.]*)\}\}((?:(?!\{\{#if ).)*?)\{\{/if\}\}", re.S)
VALUE = re.compile(r"\{\{\s*([a-z_][a-z0-9_.]*)(\?)?\s*(?:\|\s*([a-z]+)\s*)?\}\}")
RUNTIME = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")
MAX_DEPTH = 3


class TemplateError(ValueError):
    def __init__(self, missing, problems=()):
        self.missing = sorted(set(missing))
        self.problems = list(problems)
        parts = []
        if self.missing:
            parts.append("ataria.json has no value for: " + ", ".join(self.missing))
        parts += self.problems
        super().__init__("; ".join(parts))


_MISSING = object()


def lookup(profile, path):
    value = profile
    for key in path.split("."):
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return _MISSING
    return value


def present(value):
    return value is not _MISSING and value is not None and value != "" and value != [] and value != {}


def _name(entry):
    return str(entry.get("name", "")) if isinstance(entry, dict) else str(entry)


def apply_filter(value, name, path):
    if name is None:
        if isinstance(value, (dict, list)):
            raise TemplateError((), [f"{path} is a list or object; use a filter such as | join"])
        return str(value)
    if name == "json":
        return json.dumps(value, ensure_ascii=False)
    items = value if isinstance(value, list) else [value]
    if name == "join":
        return ", ".join(_name(v) for v in items)
    if name == "names":
        return ", ".join(_name(v) for v in items if _name(v))
    if name == "bullets":
        return "\n".join("- " + (_name(v) if isinstance(v, dict) else str(v)) for v in items)
    raise TemplateError((), [f"unknown filter | {name} on {path}"])


def render_text(text, profile, runtime=None, base=None, depth=0):
    """Render one template string. `base` is the folder `{{include}}` resolves against."""
    runtime = runtime or {}
    missing, problems = [], []

    if base is not None and depth < MAX_DEPTH:
        def include(match):
            target = (Path(base) / match.group(1)).resolve()
            if not target.is_relative_to(Path(base).resolve()) or not target.is_file():
                problems.append(f"include not found: {match.group(1)}")
                return ""
            try:
                return render_text(target.read_text(encoding="utf-8"), profile, runtime, base, depth + 1)
            except TemplateError as error:
                missing.extend(error.missing)
                problems.extend(error.problems)
                return ""
        text = INCLUDE.sub(include, text)

    # Innermost {{#if}} first, so sections can nest.
    while True:
        changed = False

        def section(match):
            nonlocal changed
            changed = True
            body = match.group(2)
            yes, _, no = body.partition("{{else}}")
            return yes if present(lookup(profile, match.group(1))) else no
        text = IF_BLOCK.sub(section, text)
        if not changed:
            break
    if "{{#if" in text or "{{/if}}" in text:
        problems.append("unbalanced {{#if}} / {{/if}}")

    def value(match):
        path, optional, name = match.group(1), match.group(2), match.group(3)
        found = lookup(profile, path)
        if not present(found):
            if not optional:
                missing.append(path)
            return ""
        try:
            return apply_filter(found, name, path)
        except TemplateError as error:
            problems.extend(error.problems)
            return ""
    text = VALUE.sub(value, text)
    text = RUNTIME.sub(lambda m: str(runtime.get(m.group(1), m.group(0))), text)
    if missing or problems:
        raise TemplateError(missing, problems)
    return text


def render_file(path, profile, runtime=None, base=None):
    path = Path(path)
    return render_text(path.read_text(encoding="utf-8"), profile, runtime, base or path.parent)


def fields_used(text):
    """Every profile path a template refers to, for documentation and checks."""
    return sorted({m.group(1) for m in VALUE.finditer(text)} | {m.group(1) for m in IF_BLOCK.finditer(text)})
