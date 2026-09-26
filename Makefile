.PHONY: api web pipeline reset

api:
	cd api && uvicorn main:app --reload --port 8000

web:
	cd web && npm run dev

pipeline:
	python -m engine.pipeline

reset:
	curl -X POST http://localhost:8000/api/demo/reset
