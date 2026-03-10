# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

RUB Dashboard ("Registro Único de Beneficiarios") — a social benefits monitoring dashboard that tracks beneficiaries across multiple social programs, detects incompatibilities, and provides geographic/demographic analysis.

## Commands

```bash
# Run the app (Flask dev server on http://localhost:5000)
python app.py

# Seed the database with demo data (300 beneficiaries, 8 programs, 3 periods)
python seed.py
```

No build step, no linting, no test suite configured.

## Demo Credentials

- Admin: `admin@demo.local` / `Demo123!` (full access including nominal view)
- User: `user@demo.local` / `Demo123!` (read-only, dashboard only)

## Architecture

**Stack:** Flask (Python) backend, server-rendered Jinja2 templates, Chart.js for visualizations, SQLite (WAL mode) database.

**Backend (`app.py`):** Single-file Flask app containing all routes, DB helpers, auth decorators (`@login_required`, `@admin_required`), and API endpoints. Database path hardcoded as `rub.db`.

**Templates (`templates/`):**
- `base.html` — Master layout with sidebar navigation and dark/light theme system
- `dashboard.html` — Main view with 16 KPI indicators and Chart.js charts
- `nominal.html` — Admin-only beneficiary roster with detail modal and CSV export
- `login.html` — Authentication form

**Data Model:** Users → Sessions; Beneficiaries → Benefits (per program/period) → Payments. Incompatibility rules define which program pairs conflict.

**API Routes:**
- `/api/indicators/*` — Dashboard KPIs, filtered by `?period=` query param
- `/api/nominal/*` — Beneficiary list (paginated), detail, and CSV export (admin only)

**Frontend State:** Client-side `globalChartFilters` object drives chart filtering. Theme preference stored in `localStorage`.

**Legacy files:** `app.js`, `data.js`, `index.html`, `style.css` are from an earlier client-only prototype and are not used by the Flask app.

## Key Patterns

- All DB access uses raw SQL via `get_db()` helper (returns sqlite3 connection with Row factory)
- Auth is session-based with SHA256 password hashing
- Period format: `YYYY-MM` (e.g., `2026-03`)
- CUIL is the national ID used to identify beneficiaries; "non-identified" means invalid/missing CUIL
- Charts re-render client-side when period or theme changes
