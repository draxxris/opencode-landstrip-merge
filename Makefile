# opencode-landstrip-merge -- install + test targets
PREFIX  ?= $(HOME)/.local
BINDIR  ?= $(PREFIX)/bin
DATADIR ?= $(PREFIX)/share/opencode-landstrip-merge

BIN  := opencode-landstrip-merge
SRC  := opencode-landstrip-merge.py
FUNC := shell-func-oc.sh
BASE := landstrip-default.json

INSTALLED_BIN  := $(BINDIR)/$(BIN)
INSTALLED_FUNC := $(DATADIR)/shell-func-oc.sh
# User-level baseline landstrip policy (the opencode-itself + plugins access).
# Seeded from landstrip-default.json; never overwritten once it exists so user
# edits survive re-installs.
BASELINE_FILE  ?= $(HOME)/.config/opencode/landstrip.json

.PHONY: install install-rc uninstall test check clean

install: $(INSTALLED_BIN) $(INSTALLED_FUNC) $(BASELINE_FILE)
	@echo ""
	@echo "Done. Restart your shell (or: source ~/.bashrc / ~/.zshrc) to pick up oc()."

$(INSTALLED_BIN): $(SRC)
	@mkdir -p $(BINDIR)
	@install -m 0755 $< $@
	@echo "[install] $@"

$(INSTALLED_FUNC): $(FUNC)
	@mkdir -p $(DATADIR)
	@install -m 0644 $< $@
	@echo "[install] $@"

# Seed the user-level baseline landstrip policy from landstrip-default.json.
# Idempotent and non-destructive: an existing file (possibly user-customized)
# is left untouched.
$(BASELINE_FILE): $(BASE)
	@mkdir -p $(dir $@)
	@if [ -f "$@" ]; then \
		echo "[skip]    $@ (already exists; not overwritten)"; \
	else \
		install -m 0644 $< $@; \
		echo "[install] $@"; \
	fi

# Wire oc() into ~/.bashrc and ~/.zshrc (idempotent: safe to re-run).
install-rc:
	@for rc in $(HOME)/.bashrc $(HOME)/.zshrc; do \
		[ -f "$$rc" ] || touch "$$rc"; \
		if grep -qF '$(INSTALLED_FUNC)' "$$rc"; then \
			echo "[skip]    $$rc already sources oc()"; \
		else \
			printf '\n# opencode-landstrip-merge: oc() function\n[ -f "%s" ] && . "%s"\n' \
				"$(INSTALLED_FUNC)" "$(INSTALLED_FUNC)" >> "$$rc"; \
			echo "[install] oc() sourced in $$rc"; \
		fi; \
	done

uninstall:
	@rm -f $(INSTALLED_BIN) $(INSTALLED_FUNC)
	@echo "[uninstall] removed $(INSTALLED_BIN)"
	@echo "[uninstall] removed $(INSTALLED_FUNC)"
	@echo "(baseline $(BASELINE_FILE) left in place; remove manually if desired)"
	@echo "(source lines in ~/.bashrc / ~/.zshrc left in place; remove manually if desired)"

test check:
	@mise exec -- bats test/

clean:
	@rm -rf test/tmp
