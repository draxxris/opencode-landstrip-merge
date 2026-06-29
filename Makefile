# opencode-landstrip-merge -- install + test targets
PREFIX  ?= $(HOME)/.local
BINDIR  ?= $(PREFIX)/bin
DATADIR ?= $(PREFIX)/share/opencode-landstrip-merge

BIN  := opencode-landstrip-merge
SRC  := opencode-landstrip-merge.py
FUNC := shell-func-oc.sh

INSTALLED_BIN  := $(BINDIR)/$(BIN)
INSTALLED_FUNC := $(DATADIR)/shell-func-oc.sh

.PHONY: install install-rc uninstall test check clean

install: $(INSTALLED_BIN) $(INSTALLED_FUNC)
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
	@echo "(source lines in ~/.bashrc / ~/.zshrc left in place; remove manually if desired)"

test check:
	@mise exec -- bats test/

clean:
	@rm -rf test/tmp
