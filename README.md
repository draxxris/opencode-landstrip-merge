# opencode-landstrip-merge

Merges a per-project [`landstrip`](https://github.com/landstrip/landstrip) sandbox policy with a fixed baseline, and
mirrors the project's read paths into opencode's `external_directory` permission layer --- so the OS sandbox (landstrip)
and opencode's own permission system agree on what the agent's tools may touch.

Ships a shell function `oc()` that launches `opencode` under `landstrip` using the freshly-merged policy.

## What it does

Each `oc()` invocation runs `opencode-landstrip-merge`, which:

1.  **Builds a landstrip policy (JSON)** by deep-merging:

    - the **baseline** `~/.config/opencode/landstrip.json` (a full landstrip policy --- the filesystem/network access
      opencode itself + its plugins need), with
    - the **project** file `.opencode/landstrip.json`:
      - `allowRead` → `filesystem.allowRead`
      - `allowWrite` → `filesystem.allowWrite`
    - arrays are extended and de-duplicated; `~` is kept literal (landstrip expands it).

    The baseline is **not** embedded in the script. `mise run install` seeds it from
    [`landstrip-default.json`](landstrip-default.json); if the file is missing the script refuses to run (re-run
    `mise run install`, or edit the file to customize what opencode + its plugins may touch).

2.  **Mirrors the project's read paths** into the local `./opencode.jsonc` so opencode's permission layer agrees with
    the sandbox: each `allowRead` path becomes `"<path>/**": "allow"` under `permission.external_directory`.

    - The file is **created** if absent.
    - Otherwise entries are **additively inserted** via comment-preserving text surgery (comments, formatting and any
      manually-added keys are never touched; nothing is ever removed).
    - **Both** `allowRead` and `allowWrite` paths are mirrored --- landstrip and `external_directory` are independent
      gates that both must pass for a tool to reach a path outside the project root. Trim `EXTERNAL_DIRECTORY_SOURCES`
      in the script to mirror fewer.

landstrip only reads JSON policies, so the project file is JSON.

## Prerequisites

- `landstrip` and `opencode` on `PATH`
- Go 1.22 or newer
- [`mise`](https://mise.jdx.dev/) --- to compile and run the test suite

## Install

``` sh
mise run install
```

This:

- installs `opencode-landstrip-merge` → `~/.local/bin/`
- installs `contrib/shell-func-oc.sh` → `~/.local/share/opencode-landstrip-merge/`
- seeds the baseline policy `~/.config/opencode/landstrip.json` from `landstrip-default.json` --- **only if it does not
  already exist**, so your edits are never overwritten on re-install
- `mise run install-rc` appends a guarded `source` line for the `oc()` function to **both** `~/.bashrc` and `~/.zshrc`
  (idempotent --- safe to re-run).

Then restart your shell (or `source ~/.bashrc` / `~/.zshrc`). Ensure `~/.local/bin` is on your `PATH`.

## Usage

``` sh
oc                # launch opencode under landstrip with the merged policy
oc --some-flag    # extra args are forwarded to opencode
```

In a project, drop a `.opencode/landstrip.json`:

``` json
{
    "allowRead":  ["/abs/path/to/read", "/another"],
    "allowWrite": ["~/.cache/thing"]
}
```

The matching `./opencode.jsonc` is kept in sync automatically on each `oc()`.

## Test

``` sh
mise install      # installs bats (declared in mise.toml)
mise run compile
mise run test
```

## Uninstall

``` sh
mise run uninstall
```

(The `source` lines in `~/.bashrc` / `~/.zshrc` are left in place; remove them manually if you like.)
