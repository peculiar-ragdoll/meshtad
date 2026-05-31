.PHONY: test test-cov setup install run clean lint typecheck tui tuitest dbreset venv help

TUI_TEST_DB := /tmp/meshtad_tuitest.db

PY := $(shell which python3 2>/dev/null || echo python3)
VENV_PATH := .venv

# Resolve the venv's python binary directly. Detect and recover a broken
# venv (e.g., created in Docker with a different interpreter path).
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
	$(PY) -m venv $(VENV_PATH)
	@echo "Venv created at $(VENV_PATH)"
else
	@echo "Venv OK ($(VENV_PYTHON))"
endif

setup: install

install: venv
	$(VENV_PYTHON) -m pip install -e ".[dev]"

# ---------------------------------------------------------------------------
# Test targets — install runs every time so deps stay current
# ---------------------------------------------------------------------------
test: install
	@echo "Running tests..."
	$(VENV_PYTHON) -m pytest tests/ -v

test-cov: install
	@echo "Running tests with coverage..."
	$(VENV_PYTHON) -m pytest tests/ -v --cov=meshtad --cov-report=term-missing

# ---------------------------------------------------------------------------
# Daemon / utility targets
# ---------------------------------------------------------------------------
run: install
	$(VENV_PYTHON) -m meshtad

tui: install
	@echo "Launching meshtui (quit with q or Ctrl+C)..."
	$(VENV_PYTHON) -m meshtad.tui.app

tuitest: install
	@echo "Populating TUI test database..."
	$(VENV_PYTHON) scripts/mockdata.py --db $(TUI_TEST_DB)
	@echo "Launching meshtui (quit with q or Ctrl+C)..."
	$(VENV_PYTHON) -m meshtad.tui.app --db $(TUI_TEST_DB)

dbreset:
	@echo "Removing TUI test database $(TUI_TEST_DB)"
	@rm -f $(TUI_TEST_DB)

clean:
	rm -rf $(VENV_PATH) __pycache__ .pytest_cache .coverage
	find . -name '*.pyc' -delete
	find . -name '__pycache__' -delete

lint: install
	@echo "Running linter..."
	$(VENV_PYTHON) -m ruff check meshtad/ tests/

typecheck: install
	@echo "Running type checker..."
	$(VENV_PYTHON) -m mypy meshtad/

help:
	@echo "meshtad Makefile targets:"
	@echo "  make test       — run pytest suite (auto-creates venv if missing, installs deps every time)"
	@echo "  make test-cov   — run pytest with coverage"
	@echo "  make setup      — create venv + install editable deps"
	@echo "  make install    — reinstall editable deps"
	@echo "  make run        — start the daemon"
	@echo "  make tui          — launch TUI against the live database"
	@echo "  make tuitest    — populate mock DB + launch TUI"
	@echo "  make dbreset    — remove the mock TUI test database"
	@echo "  make clean      — remove venv, caches, pyc files"
	@echo "  make lint       — run ruff linter"
	@echo "  make typecheck  — run mypy type checker"
	@echo "  make help       — show this message"
