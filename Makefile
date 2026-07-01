.PHONY: run test

run:
	uv run uvicorn main:app --reload --port 8080

test:
	uv run pytest test_main.py -v
