#!/usr/bin/env bats

# `run --separate-stderr` needs bats >= 1.5.0 (mise installs 1.13.0).
bats_require_minimum_version 1.5.0

# Tests run the source script directly (no install needed).
SCRIPT="${BATS_TEST_DIRNAME}/../opencode-landstrip-merge"

setup() {
    PROJ="$(mktemp -d)"
    export PROJ
    # The baseline is no longer embedded in the script; seed one in the temp
    # project so the default tests run against a known baseline (8 allowRead,
    # 12 allowWrite, 1 denyWrite). Tests that need a different/absent baseline pass --baseline.
    BASELINE="$PROJ/baseline.json"
    cat > "$BASELINE" <<'EOF'
{
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
            "~/.herdr/worktrees"
        ],
        "denyWrite": ["~/.cache/secret"],
        "denyRead": ["~/.ssh"],
        "allowRead": [
            "/etc/passwd",
            "/usr",
            "/var",
            "/proc",
            "~/.config/opencode",
            "~/.local",
            "~/.npm",
            "~/.ssh/config"
        ]
    },
    "network": { "allowNetwork": true }
}
EOF
}

teardown() {
    rm -rf "$PROJ"
}

# Run the merge against the temp project; extra args are appended.
merge() {
    run "$SCRIPT" \
        --baseline "$BASELINE" \
        --rules "$PROJ/.opencode/landstrip.json" \
        --jsonc "$PROJ/opencode.jsonc" \
        --out "$PROJ/policy.json" "$@"
}

# length of filesystem.<key> in a merged policy json file
plen() {  # plen <file> <filesystem key>
    go run "${BATS_TEST_DIRNAME}/helper" length "$1" "$2"
}

# exit 0 iff the file parses as JSONC (string-aware: // inside strings is safe)
valid_jsonc() {
    go run "${BATS_TEST_DIRNAME}/helper" validate "$1"
}

# write a pretty opencode.jsonc from one line per array element
write_jsonc() {
    printf '%s\n' "$@" > "$PROJ/opencode.jsonc"
}

# --------------------------------------------------------------------------- #

@test "no landstrip.json -> baseline-only policy; opencode.jsonc not created" {
    merge
    [ "$status" -eq 0 ]
    [ "$(plen "$PROJ/policy.json" allowRead)"  = "8" ]
    [ "$(plen "$PROJ/policy.json" allowWrite)" = "12" ]
    [ "$(plen "$PROJ/policy.json" denyWrite)" = "1" ]
    [ ! -e "$PROJ/opencode.jsonc" ]
}

@test "merges allowRead/allowWrite from landstrip.json into the policy" {
    mkdir -p "$PROJ/.opencode"
    printf '{"allowRead":["/srv/a","/srv/b"],"allowWrite":["~/.cache/x"]}' > "$PROJ/.opencode/landstrip.json"
    merge
    [ "$status" -eq 0 ]
    [ "$(plen "$PROJ/policy.json" allowRead)"  = "10" ]   # 8 baseline + 2
    [ "$(plen "$PROJ/policy.json" allowWrite)" = "13" ]   # 12 baseline + 1
}

@test "merges denyWrite into the policy and de-duplicates baseline entries" {
    mkdir -p "$PROJ/.opencode"
    printf '{"denyWrite":["~/.cache/secret","/srv/internal"]}' > "$PROJ/.opencode/landstrip.json"
    merge
    [ "$status" -eq 0 ]
    [ "$(plen "$PROJ/policy.json" denyWrite)" = "2" ]   # 1 baseline + 1 new
    [ "$(plen "$PROJ/policy.json" allowRead)" = "8" ]
    [ "$(plen "$PROJ/policy.json" allowWrite)" = "12" ]
}

@test "merges denyWrite alongside allowRead and allowWrite" {
    mkdir -p "$PROJ/.opencode"
    printf '{"allowRead":["/srv/project"],"allowWrite":["~/.cache/project"],"denyWrite":["/srv/internal"]}' > "$PROJ/.opencode/landstrip.json"
    merge
    [ "$status" -eq 0 ]
    [ "$(plen "$PROJ/policy.json" allowRead)" = "9" ]
    [ "$(plen "$PROJ/policy.json" allowWrite)" = "13" ]
    [ "$(plen "$PROJ/policy.json" denyWrite)" = "2" ]
}

@test "accepts a single-string denyWrite value" {
    mkdir -p "$PROJ/.opencode"
    printf '{"denyWrite":"~/.cache/single"}' > "$PROJ/.opencode/landstrip.json"
    merge
    [ "$status" -eq 0 ]
    [ "$(plen "$PROJ/policy.json" denyWrite)" = "2" ]
}

@test "invalid denyWrite value -> nonzero exit + clear stderr" {
    mkdir -p "$PROJ/.opencode"
    printf '{"denyWrite":true}' > "$PROJ/.opencode/landstrip.json"
    merge
    [ "$status" -ne 0 ]
    [[ "$output" == *"'denyWrite'"* ]]
}

@test "creates opencode.jsonc with external_directory from allowRead AND allowWrite when absent" {
    mkdir -p "$PROJ/.opencode"
    printf '{"allowRead":["/srv/projX"],"allowWrite":["~/.cache/z"],"denyWrite":["/srv/no-write"]}' > "$PROJ/.opencode/landstrip.json"
    merge
    [ "$status" -eq 0 ]
    [ -f "$PROJ/opencode.jsonc" ]
    grep -qF '"/srv/projX/**": "allow"' "$PROJ/opencode.jsonc"
    grep -qF '.cache/z/**": "allow"' "$PROJ/opencode.jsonc"   # allowWrite IS mirrored too
    ! grep -qF '/srv/no-write/**' "$PROJ/opencode.jsonc"
}

@test "denyWrite alone does not create opencode.jsonc" {
    mkdir -p "$PROJ/.opencode"
    printf '{"denyWrite":["/srv/no-write"]}' > "$PROJ/.opencode/landstrip.json"
    merge
    [ "$status" -eq 0 ]
    [ ! -e "$PROJ/opencode.jsonc" ]
}

@test "idempotent: a second run leaves opencode.jsonc unchanged" {
    mkdir -p "$PROJ/.opencode"
    printf '{"allowRead":["/srv/projX"]}' > "$PROJ/.opencode/landstrip.json"
    merge
    cp "$PROJ/opencode.jsonc" "$PROJ/first"
    merge
    diff "$PROJ/first" "$PROJ/opencode.jsonc"
}

@test "inserts a missing entry into existing external_directory; result is valid JSONC" {
    mkdir -p "$PROJ/.opencode"
    printf '{"allowRead":["/keep","/add"]}' > "$PROJ/.opencode/landstrip.json"
    write_jsonc '{' \
        '    "permission": {' \
        '        "external_directory": {' \
        '            "/keep/**": "allow"' \
        '        }' \
        '    }' \
        '}'
    merge
    [ "$status" -eq 0 ]
    grep -qF '"/keep/**": "allow"' "$PROJ/opencode.jsonc"
    grep -qF '"/add/**": "allow"'  "$PROJ/opencode.jsonc"
    valid_jsonc "$PROJ/opencode.jsonc"
}

@test "inserts the whole external_directory block when permission lacks it" {
    mkdir -p "$PROJ/.opencode"
    printf '{"allowRead":["/srv/x"]}' > "$PROJ/.opencode/landstrip.json"
    write_jsonc '{' \
        '    "permission": {' \
        '        "bash": { "ls *": "allow" }' \
        '    }' \
        '}'
    merge
    [ "$status" -eq 0 ]
    grep -qF 'external_directory' "$PROJ/opencode.jsonc"
    grep -qF '"/srv/x/**": "allow"' "$PROJ/opencode.jsonc"
    valid_jsonc "$PROJ/opencode.jsonc"
}

@test "a // inside a string value (URL) is not mistaken for a comment" {
    mkdir -p "$PROJ/.opencode"
    printf '{"allowRead":["/new/dir"]}' > "$PROJ/.opencode/landstrip.json"
    write_jsonc '{' \
        '    "permission": {' \
        '        "external_directory": {' \
        '            "https://example.com/**": "allow"' \
        '        }' \
        '    }' \
        '}'
    merge
    [ "$status" -eq 0 ]
    grep -qF 'https://example.com/**' "$PROJ/opencode.jsonc"
    grep -qF '"/new/dir/**": "allow"' "$PROJ/opencode.jsonc"
    valid_jsonc "$PROJ/opencode.jsonc"
}

@test "malformed landstrip.json -> nonzero exit + clear stderr message" {
    mkdir -p "$PROJ/.opencode"
    printf '{ broken json' > "$PROJ/.opencode/landstrip.json"
    merge
    [ "$status" -ne 0 ]
    [[ "$output" == *"not valid JSON"* ]]
}

@test "non-object landstrip.json (a bare list) -> nonzero exit + clear stderr" {
    mkdir -p "$PROJ/.opencode"
    printf '["a","b"]' > "$PROJ/.opencode/landstrip.json"
    merge
    [ "$status" -ne 0 ]
    [[ "$output" == *"must contain a JSON object"* ]]
}

@test "--out path is printed to stdout and the policy is written there" {
    mkdir -p "$PROJ/.opencode"
    printf '{"allowRead":["/srv/a"]}' > "$PROJ/.opencode/landstrip.json"
    run --separate-stderr "$SCRIPT" \
        --baseline "$BASELINE" \
        --rules "$PROJ/.opencode/landstrip.json" \
        --jsonc "$PROJ/opencode.jsonc" \
        --out "$PROJ/custom.json"
    [ "$status" -eq 0 ]
    [ "$output" = "$PROJ/custom.json" ]
    [ -f "$PROJ/custom.json" ]
}

@test "missing --baseline override -> nonzero exit + clear stderr" {
    merge --baseline /no/such/file.json
    [ "$status" -ne 0 ]
    [[ "$output" == *"baseline policy not found"* ]]
}

@test "missing default baseline (~/.config/opencode/landstrip.json) -> nonzero exit + mise install hint" {
    # The baseline is no longer embedded: with HOME pointing at an empty dir,
    # the default baseline path is absent and the script must refuse to run.
    mkdir -p "$PROJ/.opencode"
    printf '{"allowRead":["/srv/a"]}' > "$PROJ/.opencode/landstrip.json"
    local saved_home="$HOME"
    HOME="$PROJ"
    run "$SCRIPT" \
        --rules "$PROJ/.opencode/landstrip.json" \
        --jsonc "$PROJ/opencode.jsonc" \
        --out "$PROJ/policy.json"
    HOME="$saved_home"
    [ "$status" -ne 0 ]
    [[ "$output" == *"baseline policy not found"* ]]
    [[ "$output" == *"$PROJ/.config/opencode/landstrip.json"* ]]
    [[ "$output" == *"mise run install"* ]]
    [ ! -e "$PROJ/policy.json" ]
    [ ! -e "$PROJ/opencode.jsonc" ]
}

@test "non-object baseline (a bare list) -> nonzero exit + clear stderr" {
    printf '["a","b"]' > "$BASELINE"
    merge
    [ "$status" -ne 0 ]
    [[ "$output" == *"must contain a JSON object"* ]]
}

@test "malformed baseline -> nonzero exit + clear stderr" {
    printf '{ broken json' > "$BASELINE"
    merge
    [ "$status" -ne 0 ]
    [[ "$output" == *"not valid JSON"* ]]
}
