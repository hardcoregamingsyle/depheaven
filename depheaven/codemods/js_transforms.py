"""Regex codemods for well-known JS/TS package breaking changes."""

import re
from typing import Optional

RULES: dict[tuple[str, str, str], list[tuple[str, str, Optional[str]]]] = {

    # ── React 16 → 17 ─────────────────────────────────────────────────────────
    ("react", "16", "17"): [
        ("`React` import no longer required for JSX (new JSX transform)",
         r"^import React from ['\"]react['\"];?\s*$",
         None),  # don't auto-remove; tree-shaking handles it
        ("`componentWillMount` → `UNSAFE_componentWillMount` or `componentDidMount`",
         r"\bcomponentWillMount\b",
         r"UNSAFE_componentWillMount"),
        ("`componentWillReceiveProps` → `UNSAFE_componentWillReceiveProps`",
         r"\bcomponentWillReceiveProps\b",
         r"UNSAFE_componentWillReceiveProps"),
        ("`componentWillUpdate` → `UNSAFE_componentWillUpdate`",
         r"\bcomponentWillUpdate\b",
         r"UNSAFE_componentWillUpdate"),
    ],

    # ── React 17 → 18 ─────────────────────────────────────────────────────────
    ("react", "17", "18"): [
        ("`ReactDOM.render(` → `createRoot().render(` — concurrent mode",
         r"ReactDOM\.render\(",
         None),  # multi-line transform, flag only
        ("`ReactDOM.hydrate(` → `hydrateRoot(`",
         r"ReactDOM\.hydrate\(",
         None),
        ("`import ReactDOM from 'react-dom'` → `from 'react-dom/client'` for createRoot",
         r"import ReactDOM from ['\"]react-dom['\"]",
         None),
        ("`act()` from `react-dom/test-utils` → `react` directly",
         r"from ['\"]react-dom/test-utils['\"]",
         None),
        ("`flushSync` import moved to `react-dom`",
         r"flushSync",
         None),
        ("`unstable_batchedUpdates` less needed with automatic batching",
         r"unstable_batchedUpdates",
         None),
    ],

    # ── React 18 → 19 ─────────────────────────────────────────────────────────
    ("react", "18", "19"): [
        ("`ReactDOM.createRoot` still works but check for deprecated APIs",
         r"",
         None),
        ("`useFormStatus` / `useFormState` now from `react-dom`",
         r"useFormStatus|useFormState",
         None),
        ("`ref` as prop now works without `forwardRef`",
         r"forwardRef\(",
         None),
        ("`React.lazy` suspense always required",
         r"React\.lazy\(",
         None),
    ],

    # ── axios 0 → 1 ───────────────────────────────────────────────────────────
    ("axios", "0", "1"): [
        ("`axios.Cancel` → `axios.CanceledError`",
         r"\baxios\.Cancel\b(?!edError|Token)",
         r"axios.CanceledError"),
        ("`CancelToken` is deprecated — use `AbortController`",
         r"\bnew axios\.CancelToken\b|\baxios\.CancelToken\.source\b",
         None),
        ("`axios.isCancel(` still works unchanged",
         r"",
         None),
        ("`config.data` might behave differently for Content-Type detection",
         r"",
         None),
    ],

    # ── webpack 4 → 5 ─────────────────────────────────────────────────────────
    ("webpack", "4", "5"): [
        ("`require.extensions` removed",
         r"\brequire\.extensions\b",
         None),
        ("`output.futureEmitAssets` removed (now always true)",
         r"futureEmitAssets\s*:",
         None),
        ("`optimization.hashedModuleIds` → `optimization.moduleIds: 'deterministic'`",
         r"hashedModuleIds\s*:\s*true",
         r"moduleIds: 'deterministic'"),
        ("`optimization.namedModules` → `optimization.moduleIds: 'named'`",
         r"namedModules\s*:\s*true",
         r"moduleIds: 'named'"),
        ("`optimization.namedChunks` → `optimization.chunkIds: 'named'`",
         r"namedChunks\s*:\s*true",
         r"chunkIds: 'named'"),
        ("`optimization.occurrenceOrder` → `optimization.moduleIds: 'size'`",
         r"occurrenceOrder\s*:\s*true",
         None),
        ("`node.Buffer` / `node.process` config namespace changed",
         r"node\s*:\s*\{[^}]*(?:Buffer|process)",
         None),
        ("`IgnorePlugin` constructor changed to `{ resourceRegExp, contextRegExp }`",
         r"new webpack\.IgnorePlugin\(/",
         None),
        ("`file-loader` / `url-loader` → `asset` modules built in",
         r"['\"](?:file|url|raw)-loader['\"]",
         None),
    ],

    # ── express 4 → 5 ─────────────────────────────────────────────────────────
    ("express", "4", "5"): [
        ("`app.del(` → `app.delete(`",
         r"\bapp\.del\(",
         r"app.delete("),
        ("`req.param(` removed — use `req.params`, `req.body`, `req.query`",
         r"\breq\.param\(",
         None),
        ("`res.sendfile(` → `res.sendFile(`",
         r"\bres\.sendfile\(",
         r"res.sendFile("),
        ("`res.json(obj, status)` signature removed — use `res.status(n).json(obj)`",
         r"res\.json\([^,]+,\s*\d{3}\)",
         None),
        ("`res.redirect()` requires explicit path — check relative redirects",
         r"res\.redirect\(['\"][^/'\"]",
         None),
        ("`magic routing` with router.param callback API changed",
         r"router\.param\(['\"][^'\"]+['\"],\s*function",
         None),
    ],

    # ── vue 2 → 3 ─────────────────────────────────────────────────────────────
    ("vue", "2", "3"): [
        ("`new Vue({` → `createApp({`",
         r"\bnew Vue\(\{",
         None),
        ("`Vue.use(` → `app.use(`",
         r"\bVue\.use\(",
         None),
        ("`Vue.component(` → `app.component(`",
         r"\bVue\.component\(",
         None),
        ("`Vue.directive(` → `app.directive(`",
         r"\bVue\.directive\(",
         None),
        ("`Vue.mixin(` → `app.mixin(`",
         r"\bVue\.mixin\(",
         None),
        ("`Vue.set(` removed — reactivity is automatic",
         r"\bVue\.set\(",
         None),
        ("`Vue.delete(` removed — reactivity is automatic",
         r"\bVue\.delete\(",
         None),
        ("`$on/$off/$once` removed — use mitt or tiny-emitter",
         r"\.\$on\(|\.\$off\(|\.\$once\(",
         None),
        ("`filters:` option removed — use computed or methods",
         r"\bfilters\s*:\s*\{",
         None),
        ("`beforeDestroy` → `beforeUnmount`",
         r"\bbeforeDestroy\b",
         r"beforeUnmount"),
        ("`destroyed` → `unmounted`",
         r"\bdestroyed\b(?!\s*\()",
         r"unmounted"),
        ("`.native` modifier removed from `v-on`",
         r"@\w+\.native\b",
         None),
        ("`v-model` on components uses `modelValue` prop by default",
         r"v-model\s*=",
         None),
        ("`$scopedSlots` → `$slots` (merged)",
         r"\$scopedSlots",
         r"$slots"),
        ("`functional` component option removed — use plain functions",
         r"\bfunctional\s*:\s*true",
         None),
    ],

    # ── lodash 3 → 4 ──────────────────────────────────────────────────────────
    ("lodash", "3", "4"): [
        ("`_.pluck(` removed → `_.map(`",      r"\b_\.pluck\(",      r"_.map("),
        ("`_.any(` → `_.some(`",               r"\b_\.any\(",        r"_.some("),
        ("`_.all(` → `_.every(`",              r"\b_\.all\(",        r"_.every("),
        ("`_.contains(` → `_.includes(`",      r"\b_\.contains\(",   r"_.includes("),
        ("`_.first(` → `_.head(`",             r"\b_\.first\(",      r"_.head("),
        ("`_.rest(` → `_.tail(`",              r"\b_\.rest\(",       r"_.tail("),
        ("`_.object(` → `_.zipObject(`",       r"\b_\.object\(",     r"_.zipObject("),
        ("`_.flatten(arr, true)` → `_.flattenDeep(`",
         r"\b_\.flatten\(\s*(\w+)\s*,\s*true\s*\)",
         r"_.flattenDeep(\1)"),
        ("`_.pairs(` → `_.toPairs(`",          r"\b_\.pairs\(",      r"_.toPairs("),
        ("`_.where(` → `_.filter(`",           r"\b_\.where\(",      r"_.filter("),
        ("`_.findWhere(` → `_.find(`",         r"\b_\.findWhere\(",  r"_.find("),
        ("`_.invoke(` → `_.invokeMap(`",       r"\b_\.invoke\(",     r"_.invokeMap("),
        ("`_.indexBy(` → `_.keyBy(`",          r"\b_\.indexBy\(",    r"_.keyBy("),
        ("`_.sortByOrder(` → `_.orderBy(`",    r"\b_\.sortByOrder\(", r"_.orderBy("),
        ("`_.trunc(` → `_.truncate(`",         r"\b_\.trunc\(",      r"_.truncate("),
        ("`_.max/min` now use iteratees differently",
         r"\b_\.(max|min)\(\s*\w+\s*,\s*function",
         None),
    ],

    # ── jest 26 → 27 ──────────────────────────────────────────────────────────
    ("jest", "26", "27"): [
        ("`jest.genMockFromModule(` → `jest.createMockFromModule(`",
         r"\bjest\.genMockFromModule\(",
         r"jest.createMockFromModule("),
        ("`testRunner` default changed from `jasmine2` to `jest-circus`",
         r"testRunner\s*:\s*['\"]jasmine2['\"]",
         None),
        ("`testEnvironment` default changed to `node` — add `@jest-environment jsdom`",
         r"testEnvironment\s*:\s*['\"]jsdom['\"]",
         None),
    ],

    # ── jest 27 → 28 ──────────────────────────────────────────────────────────
    ("jest", "27", "28"): [
        ("`jest-jasmine2` runner removed — use jest-circus",
         r"testRunner.*jasmine",
         None),
        ("`@jest/fake-timers` API changed — `useFakeTimers('modern')` arg removed",
         r"useFakeTimers\(['\"]modern['\"]\)",
         r"useFakeTimers()"),
        ("`jest.setMock` and manual mocks — `automock` behavior tightened",
         r"automock\s*:\s*true",
         None),
    ],

    # ── jest 28 → 29 ──────────────────────────────────────────────────────────
    ("jest", "28", "29"): [
        ("`snapshotFormat` `escapeString` now defaults to `false`",
         r"escapeString\s*:\s*true",
         None),
        ("`testEnvironmentOptions` for jsdom — check `customExportConditions`",
         r"testEnvironmentOptions",
         None),
    ],

    # ── typescript 3 → 4 ──────────────────────────────────────────────────────
    ("typescript", "3", "4"): [
        ("`--experimentalDecorators` still needed for legacy decorators",
         r"",
         None),
        ("`fs.promises` type fix — `Promise<void>` not `Promise<undefined>`",
         r"",
         None),
    ],

    # ── typescript 4 → 5 ──────────────────────────────────────────────────────
    ("typescript", "4", "5"): [
        ("`--out` flag removed — use `--outFile`",
         r"\b--out\b(?!File|Dir)",
         r"--outFile"),
        ("Resolution modes `node16`/`nodenext` may affect module imports",
         r"moduleResolution.*node16|moduleResolution.*nodenext",
         None),
        ("`const enum` across files may need `isolatedModules` consideration",
         r"const enum\b",
         None),
    ],

    # ── Next.js 12 → 13 ───────────────────────────────────────────────────────
    ("next", "12", "13"): [
        ("`next/image` API changed — `layout` prop removed, use CSS instead",
         r"\blayout\s*=\s*['\"](?:fill|responsive|intrinsic|fixed)['\"]",
         None),
        ("`next/link` no longer needs `<a>` child",
         r"<Link[^>]*>\s*<a\b",
         None),
        ("`getStaticProps`/`getServerSideProps` still work in pages dir",
         r"",
         None),
        ("`next/head` → use `metadata` export in app dir",
         r"import Head from ['\"]next/head['\"]",
         None),
    ],

    # ── Next.js 13 → 14 ───────────────────────────────────────────────────────
    ("next", "13", "14"): [
        ("`experimental.appDir` flag removed — app dir is stable",
         r"experimental\s*:\s*\{[^}]*appDir",
         None),
        ("`next/font` moved — import from `next/font/google` or `next/font/local`",
         r"from ['\"]@next/font/",
         None),
    ],

    # ── Svelte 3 → 4 ──────────────────────────────────────────────────────────
    ("svelte", "3", "4"): [
        ("`SvelteComponent` generic type changed — may affect typed component usage",
         r"SvelteComponent\b",
         None),
        ("`createEventDispatcher` generics changed",
         r"createEventDispatcher<",
         None),
    ],

    # ── Vite 3 → 4 ────────────────────────────────────────────────────────────
    ("vite", "3", "4"): [
        ("`CJS Node API deprecated — use ESM or `createServer` async",
         r"require\(['\"]vite['\"]\)",
         None),
        ("`@rollup/plugin-*` peer dep version bumped",
         r"",
         None),
    ],

    # ── Vite 4 → 5 ────────────────────────────────────────────────────────────
    ("vite", "4", "5"): [
        ("`vite preview` requires explicit `--host` for network access",
         r"",
         None),
        ("`resolve.browserField` default changed",
         r"browserField\s*:",
         None),
    ],

    # ── Rollup 2 → 3 ──────────────────────────────────────────────────────────
    ("rollup", "2", "3"): [
        ("`acornInjectPlugins` removed — use `acorn` option",
         r"acornInjectPlugins",
         None),
        ("`output.externalLiveBindings` default changed to `false`",
         r"externalLiveBindings",
         None),
    ],

    # ── Mocha 9 → 10 ──────────────────────────────────────────────────────────
    ("mocha", "9", "10"): [
        ("`--file` option removed — use `spec` or `.mocharc` config",
         r"--file\b",
         None),
        ("`this.timeout` inside arrow functions doesn't work",
         r"this\.timeout\(",
         None),
    ],

    # ── Chai 4 → 5 ────────────────────────────────────────────────────────────
    ("chai", "4", "5"): [
        ("`assert.deepEqual` now uses strict deep equality",
         r"assert\.deepEqual\(",
         None),
    ],

    # ── tailwindcss 2 → 3 ─────────────────────────────────────────────────────
    ("tailwindcss", "2", "3"): [
        ("`purge:` → `content:` in tailwind.config",
         r"\bpurge\s*:",
         r"content:"),
        ("`tailwind.config.js` mode option removed (JIT is default)",
         r"\bmode\s*:\s*['\"]jit['\"]",
         None),
        ("`overflow-clip` → `overflow-hidden` class rename",
         r"\boverflow-clip\b",
         r"overflow-hidden"),
    ],
}


def _major(version: str) -> str:
    return version.strip().lstrip("^~>=<! ").split(".")[0]


def apply_js_codemods(source: str, package: str, from_ver: str, to_ver: str) -> tuple[str, list[str]]:
    """Apply transformations. Returns (modified_source, list_of_change_descriptions)."""
    from_major = _major(from_ver)
    to_major = _major(to_ver)
    pkg = package.lower()

    rules = RULES.get((pkg, from_major, to_major))
    if not rules:
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
