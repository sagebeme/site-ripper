# One command per job. Run `make install` first.
PY := .venv/bin

install:
	python3 -m venv .venv
	$(PY)/pip install -e ".[dev]"

test:
	$(PY)/pytest -q

lint:
	$(PY)/ruff check .
	$(PY)/ruff format --check .

format:
	$(PY)/ruff format .

typecheck:
	$(PY)/mypy

web:
	$(PY)/site-ripper-web

lock:
	$(PY)/pip freeze --exclude-editable > requirements.lock

.PHONY: install test lint format typecheck web lock
