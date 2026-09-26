.PHONY: api web pipeline eval test reset voice-check voice-due

PYTHON ?= python3

# Run from the repo root so `engine` is importable by the API.
api:
	$(PYTHON) -m uvicorn api.main:app --reload --port 8000

web:
	cd web && npm run dev

pipeline:
	$(PYTHON) -m engine.pipeline $(ARGS)

eval:
	$(PYTHON) -m engine.eval

test:
	$(PYTHON) -m pytest -q tests

reset:
	curl -X POST http://localhost:8000/api/demo/reset

voice-check:
	$(PYTHON) -m engine.loop.voice check

voice-due:
	$(PYTHON) -m engine.loop.voice process-due
