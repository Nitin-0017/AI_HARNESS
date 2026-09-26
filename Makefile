PYTHON ?= python3
VENV_PYTHON := .venv/bin/python

.PHONY: setup run test clean

setup:
	@$(PYTHON) -I scripts/setup.py

.venv/.phase1-ready: scripts/setup.py pyproject.toml
	@$(PYTHON) -I scripts/setup.py

run: .venv/.phase1-ready
	@PYTHONDONTWRITEBYTECODE=1 "$(VENV_PYTHON)" -I -m ai_harness $(ARGS)

test: .venv/.phase1-ready
	@PYTHONDONTWRITEBYTECODE=1 "$(VENV_PYTHON)" -I -m unittest discover -s tests -v

clean:
	@$(PYTHON) -I scripts/clean.py
