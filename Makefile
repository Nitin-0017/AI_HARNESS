PYTHON ?= python3
VENV_PYTHON := .venv/bin/python

.PHONY: setup run test clean

setup:
	@$(PYTHON) -I scripts/setup.py

.venv/.harness-ready: scripts/setup.py pyproject.toml
	@$(PYTHON) -I scripts/setup.py

run: .venv/.harness-ready
	@PYTHONDONTWRITEBYTECODE=1 "$(VENV_PYTHON)" -I -m ai_harness $(ARGS)

test: .venv/.harness-ready
	@PYTHONDONTWRITEBYTECODE=1 "$(VENV_PYTHON)" -I -m unittest discover -s tests -v

clean:
	@$(PYTHON) -I scripts/clean.py

.PHONY: demo-tools
demo-tools: .venv/.harness-ready
	@PYTHONDONTWRITEBYTECODE=1 "$(VENV_PYTHON)" -I scripts/phase2_demo.py $(ARGS)

.PHONY: demo-model
demo-model: .venv/.harness-ready
	@PYTHONDONTWRITEBYTECODE=1 "$(VENV_PYTHON)" -I scripts/phase3_demo.py $(ARGS)
