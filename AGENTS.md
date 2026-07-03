# Repository Guidelines

## Project Structure & Module Organization

Trading24 is a Python 3.12 Streamlit dashboard backed by SQLite and TradingAgents. Core code lives in `trading24/`: `runner.py` runs one ticker, `scheduler.py` runs the watchlist, `db/` owns schema and CRUD, and `dashboard/` contains UI and Plotly charts. Configuration is in `config/settings.yaml` and `config/watchlist.yaml`; manual entry points are in `scripts/`. Design references live under `audits/`, `design.md`, `_typeui/`, and `stitch_*`. Keep `data/` and `.env` local-only.

## Build, Test, and Development Commands

```bash
uv sync
```
Install dependencies from `pyproject.toml` and `uv.lock`.

```bash
uv run streamlit run trading24/dashboard/app.py
```
Start the dashboard at `http://localhost:8501`.

```bash
uv run python scripts/run_once.py NVDA 2026-06-26
uv run python scripts/run_batch.py 2026-06-26
```
Run one ticker or the full watchlist. These commands call LLMs and may incur cost.

```bash
uv run python -m py_compile trading24/dashboard/app.py trading24/dashboard/charts.py trading24/runner.py trading24/scheduler.py
```
Fast syntax check before handoff.

## Coding Style & Naming Conventions

Use 4-space indentation and type hints for new Python code. Keep functions small and named by action, e.g. `run_one`, `build_trend_figure`, `save_watchlist`. Prefer existing helpers in `trading24/db/database.py`, `config.py`, and `dashboard/app.py` over duplicating parsing or persistence logic. Keep Streamlit styling scoped with stable container keys and avoid broad global CSS selectors.

## Testing Guidelines

There is no formal test suite yet. For UI or runner changes, run `py_compile` and verify the Streamlit page in a real browser; headless screenshots often capture only the loading shell. Add future tests under `tests/` with `test_*.py`, using `pytest` for decision normalization, config parsing, and database CRUD.

## Commit & Pull Request Guidelines

Recent commits use concise Conventional-style prefixes, often with scopes: `feat(ui): ...`, `fix(ai): ...`, `ui(ai): ...`, `docs: ...`. Keep messages specific and user-facing. If committing as an agent, include the project’s `Co-Authored-By` trailer convention from `HANDOVER.md`.

Pull requests should include purpose, screenshots for UI changes, commands run, known risks, and any LLM-cost impact. Link related tasks or handoff notes when applicable.

## Security & Configuration Tips

Copy `.env.example` to `.env` locally and never commit secrets. Real analysis requires `DEEPSEEK_API_KEY`; the News analyst also needs `FRED_API_KEY`. The editable `tradingagents` dependency points to `/Users/didi/TradingAgents`; update `[tool.uv.sources]` when moving machines.

## Agent-Specific Instructions

Do not modify upstream `TradingAgents`; integrate through imports, config, selected analysts, and callbacks. Treat SQLite as the interface between backend and dashboard. Real `run_once` and `run_batch` calls cost money, so prefer existing runs when debugging UI. Preserve helpers for markdown escaping, HTML escaping, decision normalization, and Streamlit SQLite thread handling.
