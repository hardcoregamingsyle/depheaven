# DepHeaven 🔮

> Dependency Hell Fixer — analyze and auto-fix import/dependency issues across languages.

## Install

```bash
# via pip (recommended)
pip install depheaven

# via pipx (isolated)
pipx install depheaven

# via npm/pnpm/bun (requires Python backend)
npm install -g depheaven
pnpm add -g depheaven
bun add -g depheaven
```

## Usage

```bash
# Fix a single file
heaven myfile.py dephell
heaven src/app.ts dephell

# Fix an entire directory (recursive)
heaven src/ dephell
heaven . dephell

# Offline mode (no network calls)
heaven . dephell --offline

# Skip confirmation prompt
heaven . dephell --yes

# Report only (no fixes applied)
heaven . dephell --no-fix
```

## What it does

1. **Scans** source files for all import/require statements
2. **Checks** each dependency against the manifest (`requirements.txt`, `package.json`, `go.mod`)
3. **Queries** PyPI / npm registry for the latest stable version
4. **Reports** findings with color-coded output:

```
╭─ DepHeaven v0.1.0  analyzing myfile.py ─╮
│                                          │
│  Package     Current  Latest   Status    │
│  ─────────────────────────────────────  │
│  ✓ requests  2.28.0   2.31.0   up to date│
│  ⚠ django    3.2.0    4.2.0    breaking  │
│  ✗ numpy     —        1.26.4   not in manifest│
│                                          │
╰──────────────────────────────────────────╯

3 issue(s) found.
Apply fixes to manifest(s)? [Y/n]
```

5. **Fixes** manifests (`requirements.txt`, `package.json`) with updated version pins

## Supported languages

| Language          | Extensions                          | Manifest              |
|-------------------|-------------------------------------|-----------------------|
| Python            | `.py`                               | `requirements.txt`, `pyproject.toml`, `Pipfile` |
| JavaScript/TypeScript | `.js`, `.ts`, `.jsx`, `.tsx`, `.mjs`, `.cjs` | `package.json` |
| Go                | `.go`                               | `go.mod`              |

## Options

```
heaven [TARGET] dephell [OPTIONS]

  TARGET    File or directory to analyze

Options:
  --offline       Skip network calls; use only local manifest data
  --yes, -y       Apply fixes without confirmation prompt
  --no-fix        Report only; do not offer to apply fixes
  --no-recursive  Do not recurse into subdirectories
  --version       Show version and exit
  --help          Show this message and exit
```

## License

MIT
