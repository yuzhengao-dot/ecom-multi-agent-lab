.PHONY: install test run serve variant users docker

install:
	python -m pip install -r requirements.txt
	python -m pip install -e .

test:
	PYTHONPATH=src python -m pytest

run:
	python cli.py run --user U1001 --scene "618大促" --budget 500

serve:
	PYTHONPATH=src uvicorn ecom_agents.app:app --reload --port 8000

variant:
	python cli.py variant --user U1001

users:
	python cli.py users

docker:
	docker compose up --build
