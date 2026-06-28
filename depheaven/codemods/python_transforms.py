"""
AST + regex codemods for well-known Python package breaking changes.
Rule format: (description, regex_pattern, replacement_or_None)
  replacement=None means "flag for manual review, don't auto-rewrite"
"""

import re
from typing import Optional

# ─── Rule registry ────────────────────────────────────────────────────────────
# Key: (package_normalized, from_major, to_major)
# Value: list of (description, find_pattern, replace_or_None)

RULES: dict[tuple[str, str, str], list[tuple[str, str, Optional[str]]]] = {

    # ── pydantic v1 → v2 ──────────────────────────────────────────────────────
    ("pydantic", "1", "2"): [
        ("`.dict()` → `.model_dump()`",
         r"\b(\w+)\.dict\(\)",
         r"\1.model_dump()"),
        ("`.dict(` with args → `.model_dump(`",
         r"\b(\w+)\.dict\(([^)]+)\)",
         r"\1.model_dump(\2)"),
        ("`.json()` → `.model_json()`",
         r"\b(\w+)\.json\(\)",
         r"\1.model_json()"),
        ("`.json(` with args → `.model_json(`",
         r"\b(\w+)\.json\(([^)]+)\)",
         r"\1.model_json(\2)"),
        ("`.parse_obj(` → `.model_validate(`",
         r"\b(\w+)\.parse_obj\(([^)]+)\)",
         r"\1.model_validate(\2)"),
        ("`.parse_raw(` → `.model_validate_json(`",
         r"\b(\w+)\.parse_raw\(([^)]+)\)",
         r"\1.model_validate_json(\2)"),
        ("`.schema()` → `.model_json_schema()`",
         r"\b(\w+)\.schema\(\)",
         r"\1.model_json_schema()"),
        ("`.copy(` → `.model_copy(`",
         r"\b(\w+)\.copy\(([^)]*)\)",
         r"\1.model_copy(\2)"),
        ("`@validator` → `@field_validator`",
         r"@validator\(",
         r"@field_validator("),
        ("`from pydantic import validator` → `field_validator`",
         r"(from pydantic import (?:[^;\n]*\b))validator(\b)",
         r"\1field_validator\2"),
        ("`@root_validator` → `@model_validator`",
         r"@root_validator",
         r"@model_validator(mode='before')"),
        ("`from pydantic import root_validator` → `model_validator`",
         r"(from pydantic import (?:[^;\n]*\b))root_validator(\b)",
         r"\1model_validator\2"),
        ("`ValidationError` import path unchanged, but fields attr changed",
         r"\.errors\(\)\[0\]\[.fields.\]",
         None),
        ("`class Config:` → `model_config = ConfigDict(...)`",
         r"class Config:",
         None),  # structural change, flag only
        ("`.schema_json()` → `.model_json_schema()` then json.dumps",
         r"\b(\w+)\.schema_json\(\)",
         None),
    ],

    # ── django 2 → 3 ──────────────────────────────────────────────────────────
    ("django", "2", "3"): [
        ("`ugettext(` → `gettext(`",
         r"\bugettext\(",
         r"gettext("),
        ("`ugettext_lazy(` → `gettext_lazy(`",
         r"\bugettext_lazy\(",
         r"gettext_lazy("),
        ("`ugettext_noop(` → `gettext_noop(`",
         r"\bugettext_noop\(",
         r"gettext_noop("),
        ("`ungettext(` → `ngettext(`",
         r"\bungettext\(",
         r"ngettext("),
        ("`from django.utils.translation import ugettext` → `gettext`",
         r"(from django\.utils\.translation import (?:[^;\n]*\b))ugettext\b",
         r"\1gettext"),
        ("`from django.conf.urls import url` removed — use `re_path`",
         r"from django\.conf\.urls import (.*\b)(url)(\b.*)",
         r"from django.urls import \1re_path\3"),
        ("`url(r'...` → `re_path(r'...`",
         r"\burl\(r['\"]",
         r"re_path(r'"),
        ("`django.utils.encoding.force_text` → `force_str`",
         r"\bforce_text\(",
         r"force_str("),
        ("`django.utils.encoding.smart_text` → `smart_str`",
         r"\bsmart_text\(",
         r"smart_str("),
        ("`python_2_unicode_compatible` decorator removed",
         r"@python_2_unicode_compatible",
         None),
    ],

    # ── django 3 → 4 ──────────────────────────────────────────────────────────
    ("django", "3", "4"): [
        ("`force_text(` → `force_str(`",
         r"\bforce_text\(",
         r"force_str("),
        ("`smart_text(` → `smart_str(`",
         r"\bsmart_text\(",
         r"smart_str("),
        ("`ugettext` family fully removed (already in 3, now mandatory)",
         r"\bugettext\w*\(",
         None),
        ("`DEFAULT_AUTO_FIELD` must be set in settings",
         r"DEFAULT_AUTO_FIELD",
         None),
        ("`Signal(providing_args=` kwarg removed",
         r"Signal\(providing_args=",
         r"Signal("),
    ],

    # ── django 4 → 5 ──────────────────────────────────────────────────────────
    ("django", "4", "5"): [
        ("`CSRF_COOKIE_MASKED` removed",
         r"CSRF_COOKIE_MASKED",
         None),
        ("`index_together` → `indexes` with `Index`",
         r"index_together\s*=",
         None),
        ("`unique_together` still works but `UniqueConstraint` preferred",
         r"unique_together\s*=",
         None),
    ],

    # ── numpy 1 → 2 ───────────────────────────────────────────────────────────
    ("numpy", "1", "2"): [
        ("`np.bool` → `bool`",  r"\bnp\.bool\b(?!\w)",  r"bool"),
        ("`np.int` → `int`",    r"\bnp\.int\b(?!\w)",   r"int"),
        ("`np.float` → `float`", r"\bnp\.float\b(?!\w)", r"float"),
        ("`np.complex` → `complex`", r"\bnp\.complex\b(?!\w)", r"complex"),
        ("`np.object` → `object`", r"\bnp\.object\b(?!\w)", r"object"),
        ("`np.str` → `str`",    r"\bnp\.str\b(?!\w)",   r"str"),
        ("`np.long` → `np.intp`", r"\bnp\.long\b",      r"np.intp"),
        ("`np.unicode_` → `np.str_`", r"\bnp\.unicode_\b", r"np.str_"),
        ("`np.Inf` → `np.inf`", r"\bnp\.Inf\b",         r"np.inf"),
        ("`np.NaN` → `np.nan`", r"\bnp\.NaN\b",         r"np.nan"),
        ("`np.PINF` → `np.inf`", r"\bnp\.PINF\b",       r"np.inf"),
        ("`np.NINF` → `np.NINF` (unchanged but `-np.inf` preferred)",
         r"\bnp\.NINF\b", None),
        ("`np.cumproduct` → `np.cumprod`",
         r"\bnp\.cumproduct\(", r"np.cumprod("),
        ("`np.sometrue` → `np.any`",
         r"\bnp\.sometrue\(", r"np.any("),
        ("`np.alltrue` → `np.all`",
         r"\bnp\.alltrue\(", r"np.all("),
        ("`np.in1d` → `np.isin`",
         r"\bnp\.in1d\(", r"np.isin("),
        ("`np.row_stack` → `np.vstack`",
         r"\bnp\.row_stack\(", r"np.vstack("),
    ],

    # ── flask 1 → 2 ───────────────────────────────────────────────────────────
    ("flask", "1", "2"): [
        ("`@app.before_first_request` removed — move to `with app.app_context()`",
         r"@\w+\.before_first_request\b",
         None),
        ("`flask.ext.` import style removed",
         r"from flask\.ext\.",
         None),
        ("`Markup(` → `markupsafe.Markup(` (moved out of flask)",
         r"\bMarkup\(",
         None),
        ("`flask.json.JSONEncoder` → override `app.json_provider_class`",
         r"class \w+\(JSONEncoder\)",
         None),
        ("`request.json` still works; `force=True` param removed",
         r"request\.get_json\(force=True\)",
         r"request.get_json()"),
    ],

    # ── flask 2 → 3 ───────────────────────────────────────────────────────────
    ("flask", "2", "3"): [
        ("`FLASK_ENV` environment variable removed — use `FLASK_DEBUG`",
         r"FLASK_ENV",
         None),
        ("`flask.helpers.send_from_directory` → `flask.send_from_directory`",
         r"from flask\.helpers import send_from_directory",
         r"from flask import send_from_directory"),
    ],

    # ── sqlalchemy 1 → 2 ──────────────────────────────────────────────────────
    ("sqlalchemy", "1", "2"): [
        ("`session.execute(query)` now requires `select()` style",
         r"session\.execute\(\s*(\w+Query|\w+\.query)",
         None),
        ("`Query.get(pk)` → `session.get(Model, pk)`",
         r"\b(\w+)\.query\.get\(([^)]+)\)",
         r"session.get(\1, \2)"),
        ("`session.query(Model)` → `select(Model)`",
         r"\bsession\.query\(([^)]+)\)",
         None),
        ("`Column(` without type in mapped classes needs `mapped_column(`",
         r"\bColumn\((?!\s*[\w.]+\s*[,)])",
         None),
        ("`declarative_base()` → `DeclarativeBase` class",
         r"\bdeclarative_base\(\)",
         None),
        ("`relationship(` backref string → explicit `back_populates`",
         r"relationship\([^)]*backref\s*=\s*['\"]",
         None),
    ],

    # ── celery 4 → 5 ──────────────────────────────────────────────────────────
    ("celery", "4", "5"): [
        ("`task_always_eager` removed — use `CELERY_TASK_ALWAYS_EAGER` or mock",
         r"task_always_eager",
         None),
        ("`CELERY_ALWAYS_EAGER` → `task_always_eager` (settings rename)",
         r"CELERY_ALWAYS_EAGER",
         r"task_always_eager"),
        ("`app.config_from_object('django.conf:settings', namespace='CELERY')`",
         r"CELERY_BROKER_URL",
         r"broker_url"),
        ("`@task` decorator → `@shared_task` recommended",
         r"@app\.task\b",
         None),
    ],

    # ── pytest 6 → 7 ──────────────────────────────────────────────────────────
    ("pytest", "6", "7"): [
        ("`pytest.warns()` now requires `match` argument for specificity",
         r"pytest\.warns\(\w+\s*\)",
         None),
        ("`@pytest.fixture` without `scope` is still function-scoped (no change)",
         r"",
         None),
    ],

    # ── pytest 7 → 8 ──────────────────────────────────────────────────────────
    ("pytest", "7", "8"): [
        ("`--strict` flag removed — use `--strict-markers`",
         r"--strict\b(?!-)",
         r"--strict-markers"),
        ("`pytest.PytestUnraisableExceptionWarning` behavior changed",
         r"PytestUnraisableExceptionWarning",
         None),
    ],

    # ── requests 2 → 3 ────────────────────────────────────────────────────────
    ("requests", "2", "3"): [
        ("`requests.packages.urllib3` → `urllib3` directly",
         r"requests\.packages\.urllib3",
         r"urllib3"),
        ("`requests.compat` removed",
         r"from requests\.compat import",
         None),
    ],

    # ── aiohttp 2 → 3 ─────────────────────────────────────────────────────────
    ("aiohttp", "2", "3"): [
        ("`async with aiohttp.ClientSession() as session:` pattern unchanged",
         r"",
         None),
        ("`response.json()` → `await response.json()`",
         r"(?<!await\s)response\.json\(\)",
         r"await response.json()"),
        ("`aiohttp.get(` → `session.get(`",
         r"\baiohttp\.(?:get|post|put|delete|patch)\(",
         None),
    ],

    # ── click 7 → 8 ───────────────────────────────────────────────────────────
    ("click", "7", "8"): [
        ("`autocompletion=` → `shell_complete=`",
         r"\bautocompletion\s*=",
         r"shell_complete="),
        ("`@click.pass_context` combined with `auto_envvar_prefix` behavior changed",
         r"auto_envvar_prefix",
         None),
    ],

    # ── boto3 / botocore ──────────────────────────────────────────────────────
    ("boto3", "1", "2"): [
        ("`resource()` API deprecated for some services",
         r"boto3\.resource\(",
         None),
    ],

    # ── pandas 1 → 2 ──────────────────────────────────────────────────────────
    ("pandas", "1", "2"): [
        ("`DataFrame.append(` removed — use `pd.concat`",
         r"\b(\w+)\.append\(([^)]+)\)",
         r"pd.concat([\1, \2])"),
        ("`pd.DataFrame.swapaxes` removed — use `transpose`",
         r"\.swapaxes\(",
         None),
        ("`pd.io.json.build_table_schema` moved",
         r"pd\.io\.json\.build_table_schema",
         r"pd.api.interchange.from_dataframe"),
        ("`DataFrame.iteritems()` → `DataFrame.items()`",
         r"\.iteritems\(\)",
         r".items()"),
        ("`Series.iteritems()` → `Series.items()`",
         r"\.iteritems\(\)",
         r".items()"),
        ("`Index.is_monotonic` → `Index.is_monotonic_increasing`",
         r"\.is_monotonic\b(?!_)",
         r".is_monotonic_increasing"),
    ],

    # ── matplotlib 2 → 3 ──────────────────────────────────────────────────────
    ("matplotlib", "2", "3"): [
        ("`plt.hold(` removed — hold is always on",
         r"plt\.hold\(",
         None),
        ("`axes.hold(` removed",
         r"\baxes\.hold\(",
         None),
        ("`normed=` kwarg → `density=`",
         r"\bnormed\s*=\s*True",
         r"density=True"),
    ],

    # ── scipy 1 → 2 ───────────────────────────────────────────────────────────
    ("scipy", "1", "2"): [
        ("`scipy.integrate.odeint` still works but `solve_ivp` preferred",
         r"scipy\.integrate\.odeint\(",
         None),
        ("`scipy.misc.derivative` → `scipy.misc.derivative` (unchanged, just check import)",
         r"from scipy\.misc import derivative",
         None),
    ],

    # ── fastapi 0.x → 0.100+ ──────────────────────────────────────────────────
    ("fastapi", "0", "0"): [  # minor version jump but big changes
        ("`from fastapi import FastAPI` unchanged; `pydantic.v1` compat shim available",
         r"",
         None),
    ],

    # ── httpx 0 → 0.20+ ───────────────────────────────────────────────────────
    ("httpx", "0", "0"): [
        ("`httpx.get(` now creates a temporary client — prefer explicit `Client`",
         r"\bhttpx\.(get|post|put|delete|patch)\(",
         None),
    ],

    # ── attrs / attr ──────────────────────────────────────────────────────────
    ("attrs", "19", "20"): [
        ("`attr.s(` / `@attr.attrs` → `@attr.define` recommended",
         r"@attr\.s\b|@attr\.attrs\b",
         None),
        ("`attr.ib(` → `attr.field(` preferred",
         r"\battr\.ib\(",
         r"attr.field("),
    ],

    # ── marshmallow 2 → 3 ─────────────────────────────────────────────────────
    ("marshmallow", "2", "3"): [
        ("`.dump()` no longer returns (data, errors) tuple — raises on error",
         r"data,\s*errors\s*=\s*\w+\.dump\(",
         None),
        ("`.load()` no longer returns (data, errors) tuple",
         r"data,\s*errors\s*=\s*\w+\.load\(",
         None),
        ("`fields.String` → `fields.Str` (both work but Str is canonical)",
         r"fields\.String\b",
         r"fields.Str"),
        ("`@validates_schema(pass_many=True)` removed",
         r"pass_many\s*=\s*True",
         None),
        ("`strict=True` no longer needed — strict mode is default",
         r"strict\s*=\s*True",
         None),
    ],
}


def _major(version: str) -> str:
    return version.strip().lstrip("^~>=<! ").split(".")[0]


def apply_python_codemods(source: str, package: str, from_ver: str, to_ver: str) -> tuple[str, list[str]]:
    """Apply transformations. Returns (modified_source, list_of_change_descriptions)."""
    from_major = _major(from_ver)
    to_major = _major(to_ver)
    pkg = package.lower().replace("-", "_")

    rules = RULES.get((pkg, from_major, to_major))
    if not rules:
        # Try without pinning to_major (any upgrade from this major)
        for (p, fm, _tm), r in RULES.items():
            if p == pkg and fm == from_major:
                rules = r
                break
    if not rules:
        return source, []

    lines = source.splitlines(keepends=True)
    changes: list[str] = []

    for description, pattern, replacement in rules:
        if not pattern:
            continue
        if replacement is None:
            for i, line in enumerate(lines, 1):
                if re.search(pattern, line):
                    changes.append(f"[line {i}] Manual review: {description}")
        else:
            new_lines = []
            for i, line in enumerate(lines):
                new_line, count = re.subn(pattern, replacement, line)
                if count:
                    changes.append(f"[line {i+1}] Auto-fixed: {description}")
                new_lines.append(new_line)
            lines = new_lines

    return "".join(lines), changes
