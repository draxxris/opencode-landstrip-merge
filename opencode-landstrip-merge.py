#!/usr/bin/env python3
"""
opencode-landstrip-merge -- merge project landstrip rules + opencode external-directory policy.

Run from the project directory (the cwd where `oc()` was invoked). It does two
things, both derived from .opencode/landstrip.json (the per-project source of truth):

  1. Builds a merged LANDSTRIP policy as JSON. The merge is:

         baseline  (embedded in this script)          (opencode itself + plugins)
       + project   .opencode/landstrip.json
              allowRead:   [paths]   -> filesystem.allowRead
              allowWrite:  [paths]   -> filesystem.allowWrite

     Arrays are extended and de-duplicated (baseline entries first, preserved
     verbatim including their `~` form). The merged JSON is written to a temp
     file (default /tmp/opencode-scratch/landstrip-policy.json) and that PATH is
     printed to stdout -- the shell feeds it to `landstrip -p`.

  2. Mirrors the project's READ paths into the LOCAL ./opencode.jsonc so opencode's
     own permission layer agrees with the OS sandbox: each `allowRead` path becomes
     a `"<path>/**": "allow"` entry under `permission.external_directory`.

         - If ./opencode.jsonc is absent, it is created from a minimal template.
         - If present, entries are ADDITIVELY inserted: comments, formatting and any
           manually-added keys are preserved (targeted text surgery, not a full
           re-serialize). Missing entries are added; nothing is ever removed.
          - Both `allowRead` and `allowWrite` paths are mirrored into
            external_directory. landstrip and opencode's external_directory are
            independent gates that BOTH must pass for a tool to reach a path
            outside the project root, so any path the project needs (read or
            write) is allowed in both layers. Trim EXTERNAL_DIRECTORY_SOURCES
            below to mirror fewer.

Idempotent and safe to run on every `oc()` invocation: files are rewritten only
when a change is actually needed. All diagnostics go to stderr; the ONLY stdout
line is the merged policy path.

CLI (all optional; defaults shown):
    --baseline   (omitted: uses the baseline embedded in this script;
                 pass a path to override, e.g. for testing)
    --rules      .opencode/landstrip.json
    --jsonc      ./opencode.jsonc
    --out        /tmp/opencode-scratch/landstrip-policy.json
"""

import argparse
import copy
import json
import os
import sys

# Which landstrip.json lists get mirrored into opencode's external_directory.
# Which landstrip.json lists get mirrored into opencode's external_directory.
# Both by default: landstrip and external_directory are independent gates that
# both must pass, so a path the project needs (read or write) belongs in both.
# Trim this (e.g. to ("allowRead",)) to mirror fewer.
EXTERNAL_DIRECTORY_SOURCES = ("allowRead", "allowWrite")

DEFAULT_RULES = ".opencode/landstrip.json"   # per-project landstrip rules (JSON)
DEFAULT_OUT = "/tmp/opencode-scratch/landstrip-policy.json"

# Baseline landstrip policy: the access opencode ITSELF + its plugins need to run.
# Embedded so there is no external baseline file to keep in sync. `~` is left
# literal because landstrip expands it (matching the original baseline file).
BASELINE_POLICY = {
    "filesystem": {
        "allowWrite": [
            ".",
            "/tmp/opencode-scratch",
            "/tmp/landstrip",
            "~/opencode-trace",
            "~/.cache",
            "~/.local/share",
            "~/.local/state",
            "~/.npm/_logs",
            "~/.npm/_cacache",
            "~/.npm/_update-notifier-last-checked",
            "/dev/null",
            "~/.herdr/worktrees",
        ],
        "denyRead": [
            "~/.ssh",
        ],
        "allowRead": [
            "/etc/passwd",
            "/usr",
            "/var",
            "/proc",
            "~/.config/opencode",
            "~/.local",
            "~/.npm",
            "~/.ssh/config",
        ],
    },
    "network": {
        "allowNetwork": True,
    },
}


# --------------------------------------------------------------------------- #
# JSONC text surgery helpers (string/comment-aware, no full parse)
# --------------------------------------------------------------------------- #
def _skip_string(text, i):
    """text[i] == '"'. Return index just past the closing quote."""
    n = len(text)
    j = i + 1
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == '"':
            return j + 1
        j += 1
    return j  # unterminated; let caller cope


def _is_inside_string(line, pos):
    """True if `pos` in `line` lies inside an odd count of unescaped quotes."""
    cnt = 0
    j = 0
    while j < pos:
        if line[j] == "\\":
            j += 2
            continue
        if line[j] == '"':
            cnt += 1
        j += 1
    return cnt % 2 == 1


def match_brace(text, ob):
    """text[ob] == '{'. Return index of matching '}' (string/comment aware) or None."""
    depth = 0
    i = ob
    n = len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i = _skip_string(text, i)
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            i += 2
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def find_key_block(text, key):
    """
    Find the object value of string-key `key`. Returns (open_brace_idx,
    close_brace_idx) or None. A "key" is a string literal immediately followed
    (after whitespace) by ':'; we then expect its value to be a '{...}' block.
    """
    i = 0
    n = len(text)
    target = '"' + key + '"'
    while i < n:
        c = text[i]
        if c == '"':
            j = _skip_string(text, i) - 1  # index of closing quote
            lit = text[i : j + 1]
            after = j + 1
            while after < n and text[after] in " \t\r\n":
                after += 1
            if lit == target and after < n and text[after] == ":":
                k = after + 1
                while k < n and text[k] in " \t\r\n":
                    k += 1
                if k < n and text[k] == "{":
                    cb = match_brace(text, k)
                    if cb is not None:
                        return (k, cb)
            i = j + 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            i += 2
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        i += 1
    return None


def extract_keys(inner):
    """Return list of key strings inside a block body (text between { and })."""
    keys = []
    i = 0
    n = len(inner)
    while i < n:
        c = inner[i]
        if c == '"':
            j = i + 1
            buf = []
            while j < n:
                if inner[j] == "\\":
                    buf.append(inner[j + 1] if j + 1 < n else "")
                    j += 2
                    continue
                if inner[j] == '"':
                    break
                buf.append(inner[j])
                j += 1
            s = "".join(buf)
            k = j + 1
            while k < n and inner[k] in " \t\r\n":
                k += 1
            if k < n and inner[k] == ":":
                keys.append(s)
            i = j + 1
            continue
        if c == "/" and i + 1 < n and inner[i + 1] == "/":
            i += 2
            while i < n and inner[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and inner[i + 1] == "*":
            i += 2
            while i + 1 < n and not (inner[i] == "*" and inner[i + 1] == "/"):
                i += 1
            i += 2
            continue
        i += 1
    return keys


def strip_trailing_ws_and_comments(s):
    """
    Remove trailing whitespace and any trailing // line or /* */ block comment,
    iteratively. Always returns a PREFIX of s (only suffixes are ever removed),
    so len(result) conveniently locates the last significant character.
    """
    while True:
        s2 = s.rstrip()
        if s2.endswith("*/"):
            start = s2.rfind("/*")
            if start == -1:
                return s2
            s = s2[:start]
            continue
        nl = s2.rfind("\n")
        last = s2[nl + 1 :]
        # rightmost // on the last line that is NOT inside a string
        cut = -1
        search_from = len(last)
        while True:
            occ = last.rfind("//", 0, search_from)
            if occ == -1:
                break
            if not _is_inside_string(last, occ):
                cut = occ
                break
            search_from = occ
        if cut != -1:
            s = s2[: nl + 1 + cut]
            continue
        return s2


# --------------------------------------------------------------------------- #
# Path / glob helpers
# --------------------------------------------------------------------------- #
def to_glob(path):
    """Expand ~ and turn a directory path into an opencode external_directory glob."""
    p = os.path.expanduser(path)
    if p.endswith("**") or p.endswith("*"):
        return p
    if p.endswith("/"):
        return p + "**"
    return p + "/**"


def glob_base(glob_str):
    """Inverse of to_glob for dedup: strip a trailing /** or /* so '/x' and '/x/**' match."""
    s = glob_str.rstrip("/")
    for suf in ("/**", "/*"):
        if s.endswith(suf):
            return s[: -len(suf)].rstrip("/")
    return s


# --------------------------------------------------------------------------- #
# Core: landstrip policy merge
# --------------------------------------------------------------------------- #
def read_baseline(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def read_project(rules_path):
    """
    Return (allow_read, allow_write) lists from the project's landstrip JSON.
    Missing file -> (None, None). Coerces scalar values to single-element lists.
    """
    if not os.path.exists(rules_path):
        return (None, None)
    try:
        with open(rules_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        sys.stderr.write("opencode-landstrip-merge: %s is not valid JSON: %s\n" % (rules_path, e))
        sys.exit(2)
    if not isinstance(data, dict):
        sys.stderr.write("opencode-landstrip-merge: %s must contain a JSON object, got %s\n"
                         % (rules_path, type(data).__name__))
        sys.exit(2)

    def as_list(name):
        v = data.get(name)
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        if isinstance(v, list):
            return [str(x) for x in v]
        sys.stderr.write("opencode-landstrip-merge: '%s' in %s must be a list, got %s\n"
                         % (name, rules_path, type(v).__name__))
        sys.exit(2)

    return (as_list("allowRead"), as_list("allowWrite"))


def merge_policy(baseline, allow_read, allow_write):
    merged = copy.deepcopy(baseline)
    fs = merged.setdefault("filesystem", {})
    for key, extra in (("allowRead", allow_read or []), ("allowWrite", allow_write or [])):
        lst = fs.setdefault(key, [])
        for p in extra:
            if p not in lst:
                lst.append(p)
    return merged


def write_policy(policy, out_path):
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(policy, f, indent=4)
        f.write("\n")
    return out_path


# --------------------------------------------------------------------------- #
# Core: opencode.jsonc external_directory mirroring
# --------------------------------------------------------------------------- #
def derived_external_entries(allow_read, allow_write):
    paths = []
    for src in EXTERNAL_DIRECTORY_SOURCES:
        lst = allow_read if src == "allowRead" else allow_write
        if lst:
            paths.extend(lst)
    seen = set()
    entries = []
    for p in paths:
        g = to_glob(p)
        b = glob_base(g)
        if b in seen:
            continue
        seen.add(b)
        entries.append((g, json.dumps("allow")))
    return entries


def template_jsonc(entries):
    lines = [
        "{",
        '    "$schema": "https://opencode.ai/config.json",',
        "",
        "    \"permission\": {",
        "        \"external_directory\": {",
    ]
    for k, v in entries:
        lines.append("            %s: %s," % (json.dumps(k), v))
    lines += [
        "        },",
        "    },",
        "}",
        "",
    ]
    return "\n".join(lines)


def insert_entries_into_block(text, ob, cb, missing):
    """Insert (key, value_json) pairs as new lines before the block's closing }."""
    if not missing:
        return text
    line_start = text.rfind("\n", 0, cb) + 1
    close_indent = text[line_start:cb]
    if close_indent.strip() != "":
        close_indent = ""  # close brace not alone on its line; fall back
    entry_indent = close_indent + "    "
    block = "".join(entry_indent + json.dumps(k) + ": " + v + ",\n" for k, v in missing)

    inner = text[ob + 1 : cb]
    stripped = strip_trailing_ws_and_comments(inner)
    need_comma = bool(stripped) and stripped[-1] not in ",{"

    # Insert new entry lines just before the close-brace line's indentation.
    new_text = text[:line_start] + block + text[line_start:]
    if need_comma:
        last_sig_local = len(stripped) - 1  # stripped is a prefix of inner
        last_sig_global = ob + 1 + last_sig_local
        new_text = new_text[: last_sig_global + 1] + "," + new_text[last_sig_global + 1 :]
    return new_text


def insert_child_block(text, parent_ob, parent_cb, child_key, entries):
    """Insert `"child_key": { <entries> },` as the FIRST child of a parent block."""
    line_start = text.rfind("\n", 0, parent_cb) + 1
    parent_indent = text[line_start:parent_cb]
    if parent_indent.strip() != "":
        parent_indent = ""
    child_indent = parent_indent + "    "
    entry_indent = child_indent + "    "
    inner = "\n".join(entry_indent + json.dumps(k) + ": " + v + "," for k, v in entries)
    block = (
        child_indent
        + json.dumps(child_key)
        + ": {\n"
        + inner
        + "\n"
        + child_indent
        + "},\n"
    )
    # parent block body right after the opening brace
    insert_at = parent_ob + 1
    # if something follows on the same line, push to a fresh line
    return text[:insert_at] + "\n" + block + text[insert_at:]


def mirror_external_directory(jsonc_path, entries):
    """Ensure ./opencode.jsonc has the derived external_directory entries."""
    if not entries:
        return  # nothing to mirror

    if not os.path.exists(jsonc_path):
        with open(jsonc_path, "w", encoding="utf-8") as f:
            f.write(template_jsonc(entries))
        sys.stderr.write("opencode-landstrip-merge: created %s\n" % jsonc_path)
        return

    with open(jsonc_path, "r", encoding="utf-8") as f:
        text = f.read()

    existing_bases = set()
    ed = find_key_block(text, "external_directory")
    if ed is not None:
        ob, cb = ed
        for k in extract_keys(text[ob + 1 : cb]):
            existing_bases.add(glob_base(k))

    missing = [(k, v) for (k, v) in entries if glob_base(k) not in existing_bases]
    if not missing:
        return  # idempotent: nothing to add

    if ed is not None:
        ob, cb = ed
        new_text = insert_entries_into_block(text, ob, cb, missing)
    else:
        perm = find_key_block(text, "permission")
        if perm is not None:
            new_text = insert_child_block(text, perm[0], perm[1], "external_directory", missing)
        else:
            root = find_key_block(text, "$schema")  # last-ditch: top-level object
            # Fall back: insert a permission block as first child of the root object.
            # Find root '{' = first '{' in file.
            rob = text.find("{")
            rcb = match_brace(text, rob) if rob != -1 else None
            if rcb is None:
                sys.stderr.write("opencode-landstrip-merge: could not locate root object in %s\n" % jsonc_path)
                return
            # Build a permission { external_directory { ... } } block as the first
            # child of the root object.
            line_start = text.rfind("\n", 0, rcb) + 1
            indent = text[line_start:rcb]
            if indent.strip() != "":
                indent = ""
            ci = indent + "    "   # permission key indent
            cci = ci + "    "      # external_directory key indent
            eci = cci + "    "     # entries indent
            inner = "".join(eci + json.dumps(k) + ": " + v + ",\n" for k, v in missing)
            block = (
                ci + '"permission": {\n'
                + cci + '"external_directory": {\n'
                + inner
                + cci + "},\n"
                + ci + "},\n"
            )
            new_text = text[: rob + 1] + "\n" + block + text[rob + 1 :]

    if new_text != text:
        with open(jsonc_path, "w", encoding="utf-8") as f:
            f.write(new_text)
        sys.stderr.write("opencode-landstrip-merge: updated %s (+%d entry/entries)\n"
                         % (jsonc_path, len(missing)))


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(description="Merge landstrip + opencode policy for oc().")
    ap.add_argument("--baseline", default=os.environ.get("OC_LANDSTRIP_BASELINE"),
                    help="override baseline policy file (default: baseline embedded in this script)")
    ap.add_argument("--rules", default=os.environ.get("OC_LANDSTRIP_RULES", DEFAULT_RULES))
    ap.add_argument("--jsonc", default=os.environ.get("OC_OPENCODE_JSONC", "./opencode.jsonc"))
    ap.add_argument("--out", default=os.environ.get("OC_POLICY_OUT", DEFAULT_OUT))
    ap.add_argument("--no-jsonc", action="store_true",
                    help="skip mirroring into opencode.jsonc (landstrip policy only)")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="log routine info (which rules file was merged) to stderr")
    args = ap.parse_args(argv)

    if args.baseline:
        if not os.path.exists(args.baseline):
            sys.stderr.write("opencode-landstrip-merge: baseline policy not found: %s\n" % args.baseline)
            return 1
        baseline = read_baseline(args.baseline)
    else:
        baseline = copy.deepcopy(BASELINE_POLICY)
    allow_read, allow_write = read_project(args.rules)

    if allow_read is None and allow_write is None:
        # No project rules file -> policy is just the baseline.
        allow_read, allow_write = [], []
        if args.verbose:
            sys.stderr.write("opencode-landstrip-merge: no %s found; using baseline policy only\n" % args.rules)
    elif args.verbose:
        sys.stderr.write("opencode-landstrip-merge: merged %s (allowRead=%d allowWrite=%d)\n"
                         % (args.rules, len(allow_read), len(allow_write)))

    merged = merge_policy(baseline, allow_read, allow_write)
    out_path = write_policy(merged, args.out)

    if not args.no_jsonc:
        mirror_external_directory(args.jsonc, derived_external_entries(allow_read, allow_write))

    sys.stdout.write(out_path + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
