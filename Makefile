.PHONY: lint test

lint:
	uv run ruff check --fix
	uv run pyright

test:
	uv run pytest -v --cov=src --cov-report=term-missing .
