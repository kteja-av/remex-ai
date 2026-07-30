.PHONY: up down build test lint invariants demo

up:
	docker compose up -d --wait

down:
	docker compose down

build:
	docker compose build

lint:
	docker compose run --rm api sh -c "ruff check . && mypy app worker"

test:
	docker compose run --rm api pytest -q

invariants:
	docker compose run --rm api pytest -q tests/invariants

demo:
	docker compose up -d --wait
	docker compose run --rm api pytest -q tests/acceptance/test_m1_health.py tests/invariants/test_api_worker_separation.py
