.PHONY: install test dev lint

install:
	python3 -m venv .venv
	.venv/bin/pip install -e '.[dev]'

test:
	PYTHONPATH=src .venv/bin/python -m pytest -q

dev-api:
	PYTHONPATH=src .venv/bin/python -m uvicorn zhice_os_v2.api.main:app --reload --port 8002

dev-web:
	cd frontend && npm run dev

scheduler:
	PYTHONPATH=src .venv/bin/python -m zhice_os_v2.scheduler

backup:
	.venv/bin/python scripts/backup_db.py

migrate:
	.venv/bin/alembic upgrade head

lint:
	.venv/bin/ruff check src tests
