#!/usr/bin/env node
/**
 * DepHeaven npm shim
 *
 * Calls the Python `heaven` CLI if it's installed, otherwise guides the user
 * through installation.
 */

"use strict";

const { spawnSync } = require("child_process");
const { platform } = require("os");

const args = process.argv.slice(2);

/**
 * Try to run `heaven` via the given Python executable.
 * Returns true if it worked (exit code propagated), false if not found.
 */
function tryPython(py) {
  // First check if depheaven is installed as a module
  const check = spawnSync(py, ["-c", "import depheaven"], { stdio: "ignore" });
  if (check.status !== 0) return false;

  // Run: python -m depheaven.cli <args>
  const result = spawnSync(py, ["-m", "depheaven.cli", ...args], {
    stdio: "inherit",
    env: process.env,
  });

  if (result.error) return false;
  process.exit(result.status ?? 0);
  return true; // unreachable but satisfies linter
}

/**
 * Try to run the `heaven` binary directly (when pip-installed into PATH).
 */
function tryHeavenBinary() {
  const cmd = platform() === "win32" ? "heaven.exe" : "heaven";
  const result = spawnSync(cmd, args, {
    stdio: "inherit",
    env: process.env,
    shell: platform() === "win32",
  });
  if (result.error) return false;
  process.exit(result.status ?? 0);
  return true;
}

/**
 * Show installation instructions and exit with error.
 */
function showInstallHelp() {
  const lines = [
    "",
    "  DepHeaven requires Python 3.9+ to be installed.",
    "",
    "  To install the Python backend:",
    "",
    "    pip install depheaven",
    "    # or",
    "    pipx install depheaven",
    "",
    "  Once installed, this npm shim will call it automatically.",
    "",
    "  Alternatively, use the Python CLI directly:",
    "",
    "    pip install depheaven && heaven <file> dephell",
    "",
  ];
  console.error(lines.join("\n"));
  process.exit(1);
}

// Resolution order:
// 1. Try `heaven` binary in PATH (most common after `pip install depheaven`)
// 2. Try `python3 -m depheaven.cli`
// 3. Try `python -m depheaven.cli`
// 4. Show install instructions

if (!tryHeavenBinary()) {
  for (const py of ["python3", "python", "python3.11", "python3.12", "python3.10"]) {
    try {
      if (tryPython(py)) break;
    } catch (_) {
      // ignore, try next
    }
  }
  showInstallHelp();
}
