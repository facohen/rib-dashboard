# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

RUB Dashboard ("Registro Único de Beneficiarios") — a social benefits monitoring dashboard that tracks beneficiaries across multiple social programs, detects incompatibilities, and provides geographic/demographic analysis.

## Commands

```bash
# Interactive pipeline menu (main entry point)
python ingest.py

# Direct commands (also available from ingest.py menu)
python seed_pg.py              # Seed DB with 8M demo beneficiaries
python seed_pg.py --small      # Quick test with 10K
python create_matviews.py      # Create 11 materialized views
python refresh_matviews.py     # Refresh MVs after data load
python app.py                  # Dev server on http://localhost:5000
```

### ingest.py menu options
```
  Datos:
    [1] Ver estado de la base de datos
    [2] Cargar dataset (from CSV via ingest_config.py)
    [3] Limpiar periodo específico
    [4] Limpiar TODAS las tablas de datos
  Pipeline:
    [5] Crear vistas materializadas
    [6] Refrescar vistas materializadas
    [7] Ver estado de matviews
    [8] Limpiar índices redundantes (Step 0)
  Servicios:
    [9] Verificar conexión Redis
    [S] Servir dashboard (Flask dev)
```

### Environment variables
```bash
export DATABASE_URL=postgresql://postgres:postgres@localhost/rub
export REDIS_URL=redis://localhost:6379/0  # optional, falls back to SimpleCache
```

No build step, no linting, no test suite configured.

## Demo Credentials

- Admin: `admin@demo.local` / `Demo123!` (full access including nominal view)
- User: `user@demo.local` / `Demo123!` (read-only, dashboard only)

## Architecture

**Stack:** Flask (Python) backend, server-rendered Jinja2 templates, Chart.js for visualizations, PostgreSQL database, Redis cache (falls back to SimpleCache).

**Backend (`app.py`):** Single-file Flask app containing all routes, DB helpers, auth decorators (`@login_required`, `@admin_required`), and API endpoints. Connects via `DATABASE_URL` env var.

**Config (`config.py`):** PostgreSQL connection pooling via `ThreadedConnectionPool` (min=2, max=10). Helpers: `get_connection()`, `put_connection()`, `query()`, `query_one()`, `execute()`.

**Queries (`queries.py`):** All SQL queries with 3-tier strategy: MV fast path (no filters, <10ms) → mv_cross filtered path (<2s) → raw SQL fallback (if MVs don't exist).

**Templates (`templates/`):**
- `base.html` — Master layout with sidebar navigation and dark/light theme system
- `dashboard.html` — Main view with 16 KPI indicators and Chart.js charts
- `nominal.html` — Admin-only beneficiary roster with detail modal and CSV export
- `login.html` — Authentication form

**Data Model:** Users → Sessions; Beneficiaries → Benefits (per program/period) → Payments. Incompatibility rules define which program pairs conflict.

**Materialized Views:** 11 MVs (~43K rows total, ~5 MB) pre-aggregate all dashboard indicators. Created by `create_matviews.py`, refreshed by `refresh_matviews.py`.

**Cache:** Redis preferred (shared across Gunicorn workers), SimpleCache fallback (per-process). 1-hour TTL. Clear via `/api/admin/clear-cache`.

**API Routes:**
- `/api/indicators/*` — Dashboard KPIs, filtered by `?period=` query param
- `/api/nominal/*` — Beneficiary list (paginated), detail, and CSV export (admin only)
- `/api/admin/clear-cache` — Admin-only cache invalidation

**Frontend State:** Client-side `globalChartFilters` object drives chart filtering. Theme preference stored in `localStorage`.

**Legacy files:** `app.js`, `data.js`, `index.html`, `style.css` are from an earlier client-only prototype and are not used by the Flask app.

## Key Patterns

- All DB access uses raw SQL via connection pool in `config.py` (returns RealDictCursor rows)
- Auth is session-based with SHA256 password hashing
- Period format: `YYYY-MM` (e.g., `2026-03`)
- CUIL is the national ID used to identify beneficiaries; "non-identified" means invalid/missing CUIL
- Charts re-render client-side when period or theme changes
