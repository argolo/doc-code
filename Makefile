SHELL := /bin/sh

UV ?= uv

.DEFAULT_GOAL := help

.PHONY: help venv shell install clean clear build test coverage lint format typecheck audit check all

help:
	@echo "Available targets:"
	@echo "  make venv       Create the local virtual environment with uv"
	@echo "  make shell      Open a new shell with the virtual environment active"
	@echo "  make install    Sync locked development dependencies with uv"
	@echo "  make clean      Delete only known build and tool-cache artifacts"
	@echo "  make clear      Backward-compatible alias for make clean"
	@echo "  make build      Build wheel and source distribution in dist/"
	@echo "  make test       Run the test suite"
	@echo "  make coverage   Run tests and display code coverage"
	@echo "  make lint       Check code with Ruff"
	@echo "  make format     Format code with Ruff (modifies files)"
	@echo "  make typecheck  Check static types with mypy"
	@echo "  make audit      Audit locked Python dependencies with pip-audit"
	@echo "  make check      Run lint, typecheck, tests, and audit"
	@echo "  make all        Run checks and build distributions"

venv:
	$(UV) venv

shell: venv
	@echo "Opening a new shell with .venv active. Exit it with 'exit'."
	@. .venv/bin/activate && exec "$${SHELL:-/bin/sh}" -i

install:
	$(UV) sync --locked --all-extras

clean:
	rm -rf -- build dist doc_code.egg-info .pytest_cache .ruff_cache .mypy_cache
	rm -f -- .coverage

clear: clean

build: install
	$(UV) run python -m build

test: install
	$(UV) run pytest

coverage: install
	$(UV) run pytest --cov=doc_code --cov-report=term-missing

lint: install
	$(UV) run ruff check .

format: install
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

typecheck: install
	$(UV) run mypy doc_code tests

audit: install
	$(UV) run python -m pip_audit --local --skip-editable

check: lint typecheck test audit

all: check build
