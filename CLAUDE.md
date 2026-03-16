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
python create_matviews.py      # Create 2 physical tables (mv_cross + mv_resumen)
python refresh_matviews.py     # Blue/green refresh after data load
python app.py                  # Dev server on http://localhost:5000
```

### ingest.py menu options
```
  Setup:
    [I] Inicializar base de datos (schema + índices + usuarios)
    [D] Seedear datos demo (10K/100K/1M/8M)
  Datos:
    [1] Ver estado de la base de datos
    [2] Cargar dataset (from CSV via ingest_config.py)
    [3] Limpiar periodo específico
    [4] Limpiar TODAS las tablas de datos
  Pipeline:
    [5] Crear tablas materializadas (mv_cross + mv_resumen)
    [6] Refrescar tablas materializadas
    [7] Ver estado de tablas materializadas
    [8] Limpiar índices redundantes (Step 0)
  Verificación:
    [C] Check consistencia DB ↔ MVs ↔ API
  Servicios:
    [S] Servir dashboard (Flask dev)
```

### Environment variables
```bash
export DATABASE_URL=postgresql://postgres:postgres@localhost/rub
```

No build step, no linting, no test suite configured.

## Demo Credentials

- Admin: `admin@demo.local` / `Demo123!` (full access including nominal view)
- User: `user@demo.local` / `Demo123!` (read-only, dashboard only)

## Architecture

**Stack:** Flask (Python) backend, server-rendered Jinja2 templates, Chart.js for visualizations, PostgreSQL database, FileSystemCache (`.cache/` dir, shared across Gunicorn workers).

**Backend (`app.py`):** Single-file Flask app containing all routes, DB helpers, auth decorators (`@login_required`, `@admin_required`), and API endpoints. Connects via `DATABASE_URL` env var.

**Config (`config.py`):** PostgreSQL connection pooling via `ThreadedConnectionPool` (min=2, max=10). Helpers: `get_connection()`, `put_connection()`, `query()`, `query_one()`, `execute()`.

**Queries (`queries.py`):** All SQL queries with `_use_resumen()` routing: `mv_resumen` for deduplicated person totals, `mv_cross` for per-program breakdowns. Raw SQL fallback for departamento only.

**Templates (`templates/`):**
- `base.html` — Master layout with sidebar navigation and dark/light theme system
- `dashboard.html` — Main view with 16 KPI indicators and Chart.js charts
- `nominal.html` — Admin-only beneficiary roster with detail modal and CSV export
- `login.html` — Authentication form

**Data Model:** Users → Sessions; Beneficiaries → Benefits (per program/period) → Payments. Incompatibility rules define which program pairs conflict.

**Materialized Tables:** 2 physical tables with blue/green refresh:
- `mv_cross` (~130-180K rows) — per-program breakdown with 7 dimensions
- `mv_resumen` (~23K rows) — deduplicated persons across programs (no programa/secretaria dimension)

Created by `create_matviews.py`, refreshed by `refresh_matviews.py` (shadow tables + atomic swap).

**Cache:** FileSystemCache (`.cache/` dir, shared across Gunicorn workers). 1-hour TTL. Clear via `/api/admin/clear-cache`.

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
