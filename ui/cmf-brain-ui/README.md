# CMF Brain UI

Brain-shaped view of the Cognitive Memory Fabric for the wealth-advisor use case.
Working / Episodic / Semantic / Procedural memories sit in their brain regions; click a node for evidence quotes, version history and the lifecycle audit trail; ask a question to see the retrieved memories light up with a cited report.

Run (two terminals, from the repo root; full setup, env variables and troubleshooting are in the root [README](../../README.md#run-the-app-backend--frontend)):

```bash
# 1. API: reads the memory DB; /api/ask also runs the agent pipeline, so it needs the LLM configured in .env
PYTHONPATH=src .venv/bin/python -m api.main                    # port from CMF_API_PORT (default 8000)

# 2. UI
cd ui/cmf-brain-ui && npm install && npm run dev               # port from VITE_DEV_PORT (default 5173)
```

Configuration lives in the repo-root `.env` (this UI reads only its `VITE_*` variables). The memory DB must have been populated by `python -m orchestration.cli ingest --client CLIENT-001`.
Note: every `/api/ask` stores working memories (4 h TTL), so the Working count grows as you ask.
