run:
    uv run uvicorn main:app --host 0.0.0.0 --port 8080

lint:
    ruff check .

format:
    ruff format .

test:
    uv run pytest
