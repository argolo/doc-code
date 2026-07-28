SHELL := /bin/sh

VENV ?= .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.DEFAULT_GOAL := help

.PHONY: help venv shell install clear build test coverage lint format typecheck audit check

help:
	@echo "Available targets:"
	@echo "  make venv       Create the local virtual environment"
	@echo "  make shell      Open a new shell with the virtual environment active"
	@echo "  make install    Install Commitar and all development dependencies"
	@echo "  make clear      Delete every ignored file (git clean -Xdf)"
	@echo "  make build      Build wheel and source distribution in dist/"
	@echo "  make test       Run the test suite"
	@echo "  make coverage   Run tests and display code coverage"
	@echo "  make lint       Check code with Ruff"
	@echo "  make format     Format code with Ruff"
	@echo "  make typecheck  Check static types with mypy"
	@echo "  make audit      Audit Python dependencies with pip-audit"
	@echo "  make check      Run lint, typecheck, tests, and audit"

venv:
	@test -x "$(PYTHON)" || python3 -m venv "$(VENV)"

shell: venv
	@echo "Opening a new shell with $(VENV) active. Exit it with 'exit'."
	@. "$(VENV)/bin/activate" && exec "$${SHELL:-/bin/sh}" -i

install: venv
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

clear:
	@echo "Deleting files ignored by Git, including virtual environments and local .env files."
	git clean -Xdf

build: install
	$(PYTHON) -m build

test: install
	$(PYTHON) -m pytest

coverage: install
	$(PYTHON) -m pytest --cov=commitar --cov-report=term-missing

lint: install
	$(PYTHON) -m ruff check .

format: install
	$(PYTHON) -m ruff check --fix .
	$(PYTHON) -m ruff format .

typecheck: install
	$(PYTHON) -m mypy commitar tests

audit: install
	$(PYTHON) -m pip_audit

check: lint typecheck test audit
