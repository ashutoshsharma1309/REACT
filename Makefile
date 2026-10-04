.PHONY: install run seed test migrate docker-up docker-down demo

install:
	python -m venv .venv && .venv/bin/pip install -r requirements.txt

run:
	.venv/bin/uvicorn app.main:app --reload --port 8000

seed:
	.venv/bin/python -m scripts.seed

migrate:
	.venv/bin/alembic upgrade head

test:
	.venv/bin/pytest

docker-up:
	docker compose up --build

docker-down:
	docker compose down -v

demo:
	bash scripts/demo.sh
