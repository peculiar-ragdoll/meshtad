.PHONY: test test-cov setup install lock sync run clean lint format typecheck audit bandit security tui tuitest dbreset check help

TUI_TEST_DB := /tmp/meshtad_tuitest.db
VENV_PATH := .venv
UV := $(shell which uv 2>/dev/null || echo uv)

# Resolve the venv's python binary directly.
VENV_PYTHON := $(VENV_PATH)/bin/python

# Is the venv's interpreter present AND actually runnable on this host?
# "broken" covers both missing and wrong-architecture (e.g. a Docker-built venv
# whose binary cannot exec on the macOS host).
VENV_PYTHON_OK := $(shell test -x $(VENV_PYTHON) && $(VENV_PYTHON) --version >/dev/null 2>&1 && echo ok || echo broken)

# ---------------------------------------------------------------------------
# Virtualenv bootstrap (create if missing, recreate if broken)
# ---------------------------------------------------------------------------
# `venv` is PHONY so it is re-checked on every run. A file target on
# $(VENV_PYTHON) would be considered up-to-date whenever the file merely exists
# — including when it is a wrong-arch interpreter — so make would skip the
# recreate recipe, which is the exact failure we need to recover from.
venv:
ifeq ($(VENV_PYTHON_OK),broken)
	@echo "Venv interpreter missing or broken, recreating..."
	@rm -rf $(VENV_PATH)
	$(UV) venv $(VENV_PATH)
	@echo "Venv created at $(VENV_PATH)"
else
	@echo "Venv OK ($(VENV_PYTHON))"
endif

# ---------------------------------------------------------------------------
# Setup / install — uv sync from lockfile (pins everything)
# ---------------------------------------------------------------------------
setup: lock sync

# Generate/update uv.lock from pyproject.toml
lock:
	@echo "Locking dependencies..."
	$(UV) lock

# Sync venv to match uv.lock (includes -e . + dev extras)
sync: venv
	@echo "Syncing dependencies..."
	$(UV) sync --extra dev

# ---------------------------------------------------------------------------
# Test targets — sync runs every time so deps stay current
# ---------------------------------------------------------------------------
test: sync
	@echo "Running tests..."
	$(VENV_PYTHON) -m pytest tests/ -v

test-cov: sync
	@echo "Running tests with coverage..."
	$(VENV_PYTHON) -m pytest tests/ -v --cov=meshtad --cov-report=term-missing

# ---------------------------------------------------------------------------
# Daemon / utility targets
# ---------------------------------------------------------------------------
run: sync
	$(VENV_PYTHON) -m meshtad

tui: sync
	@echo "Launching meshtui (quit with q or Ctrl+C)..."
	$(VENV_PYTHON) -m meshtad.tui.app

tuitest: sync
	@echo "Populating TUI test database..."
	$(VENV_PYTHON) scripts/mockdata.py --db $(TUI_TEST_DB)
	@echo "Launching meshtui (quit with q or Ctrl+C)..."
	$(VENV_PYTHON) -m meshtad.tui.app --db $(TUI_TEST_DB)

dbreset:
	@echo "Removing TUI test database $(TUI_TEST_DB)"
	@rm -f $(TUI_TEST_DB)

# ---------------------------------------------------------------------------
# Linting / formatting / type-checking
# ---------------------------------------------------------------------------
lint: sync
	@echo "Running linter..."
	$(VENV_PYTHON) -m ruff check meshtad/ tests/

format: sync
	@echo "Running formatter..."
	$(VENV_PYTHON) -m ruff format meshtad/ tests/

# Format + lint in one shot
check: format lint

typecheck: sync
	@echo "Running type checker..."
	$(VENV_PYTHON) -m mypy meshtad/

# ---------------------------------------------------------------------------
# Security — dependency CVEs + static code scans
# ---------------------------------------------------------------------------
audit: sync
	@echo "Scanning dependencies for known CVEs (pip-audit)..."
	$(VENV_PYTHON) -m pip_audit -f json

bandit: sync
	@echo "Scanning source for security issues (bandit)..."
	$(VENV_PYTHON) -m bandit -r meshtad/ -lll

# Both scans in one shot
security: audit bandit

# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------
clean:
	rm -rf $(VENV_PATH) __pycache__ .pytest_cache .coverage uv.lock
	find . -name '*.pyc' -delete
	find . -name '__pycache__' -delete

help:
	@echo "meshtad Makefile targets:"
	@echo "  make setup      — create venv + lock + sync deps (first-time bootstrap)"
	@echo "  make lock       — generate/update uv.lock from pyproject.toml"
	@echo "  make sync       — sync venv to match uv.lock (includes -e .[dev])"
	@echo "  make test       — run pytest suite"
	@echo "  make test-cov   — run pytest with coverage"
	@echo "  make run        — start the daemon"
	@echo "  make tui        — launch TUI against the live database"
	@echo "  make tuitest    — populate mock DB + launch TUI"
	@echo "  make dbreset    — remove the mock TUI test database"
	@echo "  make lint       — run ruff linter"
	@echo "  make format     — run ruff formatter"
	@echo "  make check      — format + lint"
	@echo "  make typecheck  — run mypy type checker"
	@echo "  make audit      — scan dependencies for known CVEs (pip-audit)"
	@echo "  make bandit     — static security analysis of source (bandit)"
	@echo "  make security   — run both audit + bandit"
	@echo "  make clean      — remove venv, caches, lock file"
	@echo "  make help       — show this message"
