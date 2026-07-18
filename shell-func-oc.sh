# oc() -- launch opencode under landstrip with a per-project merged policy.
#
# Sourced by ~/.bashrc and ~/.zshrc (wired in by `mise run install-rc`). Requires that
# `opencode-landstrip-merge`, `landstrip`, and `opencode` are on PATH.
#
# Runs in a subshell so an EXIT trap cleans up the per-invocation policy tmpdir
# on any exit (normal, error, or interrupt). mktemp gives each oc() a unique
# policy file, so concurrent launches in different projects never clobber each
# other.
oc() {
    mkdir -p /tmp/opencode-scratch
    (
        export TMPDIR=/tmp/landstrip
        mkdir -p "$TMPDIR"
        local tmpdir merged_policy
        tmpdir="$(mktemp -d /tmp/opencode-scratch/landstrip.XXXXXX)" || {
            echo "oc: mktemp failed" >&2
            exit 1
        }
        merged_policy="$tmpdir/policy.json"
        trap 'rm -rf "$tmpdir"' EXIT
        if ! opencode-landstrip-merge --out "$merged_policy" >/dev/null; then
            echo "oc: policy merge failed (run: opencode-landstrip-merge -v)" >&2
            exit 1
        fi
        landstrip --trap-fd 3 -p "$merged_policy" opencode --log-level DEBUG "$@" 3> /tmp/opencode-landstrip.out
    )
}
