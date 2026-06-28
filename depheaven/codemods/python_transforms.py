"""
AST + regex codemods for well-known Python package breaking changes.
Each transform is keyed by (package, from_major) → (to_major, list_of_rules).
"""

import re
import ast
from typing import Optional


# ─── Rule registry ────────────────────────────────────────────────────────────
# Each rule is a (description, find_pattern, replace_pattern) tuple.
# Patterns are Python regex strings applied line-by-line (or to whole source).
# Use None for replace to mean "flag for manual review".

RULES: dict[tuple[str, str, str], list[tuple[str, str, Optional[str]]]] = {

    # pydantic v1 → v2
    ("pydantic", "1", "2"): [
        ("`.dict()` → `.model_dump()`",
         r"\b(\w+)\.dict\(\)",
         r"\1.model_dump()"),
        ("`.json()` → `.model_json()`",
         r"\b(\w+)\.json\(\)",
         r"\1.model_json()"),
        ("`@validator` → `@field_validator`",
         r"@validator\(",
         r"@field_validator("),
        ("`from pydantic import validator` → `from pydantic import field_validator`",
         r"from pydantic import (.*\b)validator(\b.*)",
         r"from pydantic import \1field_validator\2"),
        ("`.parse_obj(` → `.model_validate(`",
         r"\b(\w+)\.parse_obj\(",
         r"\1.model_validate("),
        ("`.parse_raw(` → `.model_validate_json(`",
         r"\b(\w+)\.parse_raw\(",
         r"\1.model_validate_json("),
        ("`.schema()` → `.model_json_schema()`",
         r"\b(\w+)\.schema\(\)",
         r"\1.model_json_schema()"),
    ],

    # django 2 → 3
    ("django", "2", "3"): [
        ("`from django.utils.translation import ugettext` → `gettext`",
         r"from django\.utils\.translation import (.*\b)ugettext(\b.*)",
         r"from django.utils.translation import \1gettext\2"),
        ("`ugettext(` → `gettext(`",
         r"\bugettext\(",
         r"gettext("),
        ("`ugettext_lazy(` → `gettext_lazy(`",
         r"\bugettext_lazy\(",
         r"gettext_lazy("),
        ("`from django.conf.urls import url` → `from django.urls import re_path`",
         r"from django\.conf\.urls import (.*\b)url(\b.*)",
         r"from django.urls import \1re_path\2"),
        ("`url(` → `re_path(` (django.conf.urls)",
         r"\burl\(r['\"]",
         r"re_path(r'"),
    ],

    # django 3 → 4
    ("django", "3", "4"): [
        ("`from django.utils.encoding import force_text` → `force_str`",
         r"from django\.utils\.encoding import (.*\b)force_text(\b.*)",
         r"from django.utils.encoding import \1force_str\2"),
        ("`force_text(` → `force_str(`",
         r"\bforce_text\(",
         r"force_str("),
        ("`smart_text(` → `smart_str(`",
         r"\bsmart_text\(",
         r"smart_str("),
    ],

    # numpy 1 → 2
    ("numpy", "1", "2"): [
        ("`np.bool` → `bool` (deprecated alias removed)",
         r"\bnp\.bool\b(?!\w)",
         r"bool"),
        ("`np.int` → `int` (deprecated alias removed)",
         r"\bnp\.int\b(?!\w)",
         r"int"),
        ("`np.float` → `float` (deprecated alias removed)",
         r"\bnp\.float\b(?!\w)",
         r"float"),
        ("`np.complex` → `complex` (deprecated alias removed)",
         r"\bnp\.complex\b(?!\w)",
         r"complex"),
        ("`np.object` → `object` (deprecated alias removed)",
         r"\bnp\.object\b(?!\w)",
         r"object"),
        ("`np.str` → `str` (deprecated alias removed)",
         r"\bnp\.str\b(?!\w)",
         r"str"),
    ],

    # flask 1 → 2
    ("flask", "1", "2"): [
        ("`@app.before_first_request` removed — move logic to startup",
         r"@\w+\.before_first_request",
         None),  # flag only, no auto-fix
        ("`flask.ext.` imports removed",
         r"from flask\.ext\.",
         None),
    ],

    # sqlalchemy 1 → 2
    ("sqlalchemy", "1", "2"): [
        ("`session.execute(query)` → `session.execute(select(Model))`",
         r"session\.execute\((\w+Query)",
         None),  # flag for manual review
        ("`Column(` without type not allowed in 2.x mapped classes",
         r"Column\(\s*\)",
         None),
        ("`from sqlalchemy import engine` patterns updated",
         r"from sqlalchemy import (.*\b)engine(\b.*)",
         None),
    ],

    # requests (rare but let's cover common gotcha)
    ("requests", "2", "3"): [
        ("`requests.get(..., verify=False)` — still works but generates warning",
         r"requests\.\w+\(.*verify\s*=\s*False",
         None),
    ],
}


def _major(version: str) -> str:
    return version.strip().lstrip("^~>=<! ").split(".")[0]


def apply_python_codemods(source: str, package: str, from_ver: str, to_ver: str) -> tuple[str, list[str]]:
    """
    Apply known transformations to Python source for the given package upgrade.
    Returns (modified_source, list_of_change_descriptions).
    """
    from_major = _major(from_ver)
    to_major = _major(to_ver)

    key = (package.lower().replace("-", "_"), from_major, to_major)
    rules = RULES.get(key)

    if not rules:
        # Try partial match (any to_major)
        for (pkg, frm, _to), r in RULES.items():
            if pkg == key[0] and frm == from_major:
                rules = r
                break

    if not rules:
        return source, []

    lines = source.splitlines(keepends=True)
    changes: list[str] = []

    for description, pattern, replacement in rules:
        if replacement is None:
            # Flag-only: scan and annotate but don't change
            for i, line in enumerate(lines, 1):
                if re.search(pattern, line):
                    changes.append(f"[line {i}] Manual review needed: {description}")
        else:
            new_lines = []
            for i, line in enumerate(lines):
                new_line, count = re.subn(pattern, replacement, line)
                if count:
                    changes.append(f"[line {i+1}] {description}")
                new_lines.append(new_line)
            lines = new_lines

    return "".join(lines), changes
