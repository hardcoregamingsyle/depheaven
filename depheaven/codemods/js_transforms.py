"""
Regex codemods for well-known JS/TS package breaking changes.
"""

import re
from typing import Optional

RULES: dict[tuple[str, str, str], list[tuple[str, str, Optional[str]]]] = {

    # React 17 → 18
    ("react", "17", "18"): [
        ("`ReactDOM.render(` → `createRoot().render(`",
         r"ReactDOM\.render\(",
         None),  # complex transform, flag only
        ("`ReactDOM.hydrate(` → `hydrateRoot(`",
         r"ReactDOM\.hydrate\(",
         None),
        ("`import ReactDOM from 'react-dom'` — split into react-dom/client for createRoot",
         r"import ReactDOM from ['\"]react-dom['\"]",
         None),
    ],

    # React 16 → 17
    ("react", "16", "17"): [
        ("`React` no longer needs to be in scope for JSX (React 17+)",
         r"^import React from ['\"]react['\"];?\s*$",
         None),
    ],

    # axios 0 → 1
    ("axios", "0", "1"): [
        ("`axios.Cancel` → `axios.CanceledError`",
         r"\baxios\.Cancel\b",
         r"axios.CanceledError"),
        ("`axios.isCancel(` unchanged but `CancelToken` is deprecated",
         r"\bCancelToken\b",
         None),
    ],

    # webpack 4 → 5
    ("webpack", "4", "5"): [
        ("`require.extensions` removed",
         r"\brequire\.extensions\b",
         None),
        ("`output.futureEmitAssets` removed",
         r"futureEmitAssets",
         None),
        ("`optimization.hashedModuleIds` → `optimization.moduleIds: 'deterministic'`",
         r"hashedModuleIds\s*:\s*true",
         r"moduleIds: 'deterministic'"),
        ("`optimization.namedModules` → `optimization.moduleIds: 'named'`",
         r"namedModules\s*:\s*true",
         r"moduleIds: 'named'"),
    ],

    # express 4 → 5
    ("express", "4", "5"): [
        ("`res.redirect()` always requires an absolute URL or path in v5",
         r"res\.redirect\(\s*['\"][^/'\"]",
         None),
        ("`app.del(` → `app.delete(`",
         r"\bapp\.del\(",
         r"app.delete("),
        ("`req.param(` removed — use `req.params`, `req.body`, or `req.query`",
         r"\breq\.param\(",
         None),
    ],

    # vue 2 → 3
    ("vue", "2", "3"): [
        ("`new Vue({` → `createApp({`",
         r"\bnew Vue\(\{",
         None),
        ("`Vue.set(` → reactive() or ref()",
         r"\bVue\.set\(",
         None),
        ("`Vue.delete(` → reactive() or ref()",
         r"\bVue\.delete\(",
         None),
        ("`$on/$off/$once` removed — use mitt or a similar event bus",
         r"\$on\(|\$off\(|\$once\(",
         None),
        ("`filter` option removed in Vue 3",
         r"\bfilters\s*:\s*\{",
         None),
    ],

    # lodash 3 → 4
    ("lodash", "3", "4"): [
        ("`_.pluck(` removed → use `_.map(`",
         r"\b_\.pluck\(",
         r"_.map("),
        ("`_.any(` → `_.some(`",
         r"\b_\.any\(",
         r"_.some("),
        ("`_.all(` → `_.every(`",
         r"\b_\.all\(",
         r"_.every("),
        ("`_.contains(` → `_.includes(`",
         r"\b_\.contains\(",
         r"_.includes("),
    ],

    # jest 26 → 27
    ("jest", "26", "27"): [
        ("`jest.genMockFromModule` → `jest.createMockFromModule`",
         r"\bjest\.genMockFromModule\(",
         r"jest.createMockFromModule("),
    ],

    # jest 27 → 28
    ("jest", "27", "28"): [
        ("`testEnvironment` default changed from jsdom to node",
         r"testEnvironment\s*:\s*['\"]jsdom['\"]",
         None),
    ],

    # typescript 4 → 5
    ("typescript", "4", "5"): [
        ("`--out` flag removed — use `--outFile`",
         r"\b--out\b(?!File|Dir)",
         r"--outFile"),
    ],
}


def _major(version: str) -> str:
    return version.strip().lstrip("^~>=<! ").split(".")[0]


def apply_js_codemods(source: str, package: str, from_ver: str, to_ver: str) -> tuple[str, list[str]]:
    """
    Apply known transformations to JS/TS source for the given package upgrade.
    Returns (modified_source, list_of_change_descriptions).
    """
    from_major = _major(from_ver)
    to_major = _major(to_ver)

    key = (package.lower(), from_major, to_major)
    rules = RULES.get(key)

    if not rules:
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
