.PHONY: setup init run worker test demo docker
setup:
	python scripts/setup.py
init:
	python -m app.manage init
run:
	python -m uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8000
worker:
	python -m app.worker
test:
	python -m pytest --cov=app --cov-report=term-missing
demo:
	python -m app.manage seed-demo
docker:
	docker compose -f compose.yaml -f compose.full.yaml up --build
