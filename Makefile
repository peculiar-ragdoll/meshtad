.PHONY: test test-cov setup install run clean lint typecheck tuitest dbreset help

TUI_TEST_DB := /tmp/meshtad_tuitest.db

PY := $(shell which python3 2>/dev/null || echo python3)
VENV_PATH := .venv

# Resolve the venv's python binary directly. Detect and recover a broken
# venv (e.g., created in Docker with a different interpreter path).
VENV_PYTHON := $(VENV_PATH)/bin/python
VENV_PIP := $(VENV_PATH)/bin/pip

# Check if the venv's interpreter is actually executable
VENV_PYTHON_OK := $(shell test -x $(VENV_PYTHON) && $(VENV_PYTHON) --version >/dev/null 2>&1 && echo ok || echo broken)

# ---------------------------------------------------------------------------
# Virtualenv bootstrap (recreate if broken)
# ---------------------------------------------------------------------------
$(VENV_PYTHON):
ifeq ($(VENV_PYTHON_OK),broken)
	@echo "Venv interpreter is broken, recreating..."
	@rm -rf $(VENV_PATH)
else
	@echo "Creating venv..."
endif
	$(PY) -m venv $(VENV_PATH)
	@echo "Venv created at $(VENV_PATH)"

$(VENV_PIP): $(VENV_PYTHON)

setup: install

install: $(VENV_PIP)
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
	@echo "  make tuitest    — populate mock DB + launch TUI"
	@echo "  make dbreset    — remove the mock TUI test database"
	@echo "  make clean      — remove venv, caches, pyc files"
	@echo "  make lint       — run ruff linter"
	@echo "  make typecheck  — run mypy type checker"
	@echo "  make help       — show this message"
