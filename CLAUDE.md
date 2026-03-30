# CLAUDE.md

RIB Dashboard ("Registro Integral de Beneficiarios") — social benefits monitoring dashboard for Argentina. Two perspectives: TD (Titular de Derecho, beneficiary) and TC (Titular de Cobro, payment holder).

## Commands

```bash
python ingest.py               # Interactive pipeline menu (main entry point)
python seed_pg.py [--small|--medium|--1m]  # Seed demo data (default 8M)
python create_matviews.py      # Create 5 materialized tables (TD + TC)
python refresh_matviews.py     # Blue/green refresh
python app.py                  # Dev server :5000
```

```bash
export DATABASE_URL=postgresql://postgres:postgres@localhost/rib_dev
```

No build step, no linting, no test suite.

### Comandos que NO debe ejecutar Claude

Claude **NO debe ejecutar** estos comandos. Pedir al usuario que los ejecute:

`ingest.py`, `seed_pg.py`, `create_matviews.py`, `refresh_matviews.py`, `matviews.py`, `app.py`, ni cualquier comando que inicie/reinicie/mate procesos del pipeline.

## Demo Credentials

- Admin: `admin@demo.local` / `Demo123!`
- User: `user@demo.local` / `Demo123!`

## Key Patterns

- Raw SQL via `config.py` connection pool (`RealDictCursor`)
- Period format: `YYYY-MM` (e.g., `2026-03`)
- CUIL = national ID; "non-identified" = invalid/missing CUIL
- Province normalization: INDEC codes, ANSES codes, ~40 naming variants (`ingest_config.py`)
- Filter keys: `secretaria`, `sexo`, `programa`, `provincia`, `grupo_etario`, `cant_prestaciones`
- Query routing: `use_resumen()` picks `mv_resumen*` (fast, deduped) vs `mv_cross*` (per-program detail)
- Auth: session-based, legacy SHA256 auto-upgrades to pbkdf2 on login
- Cache: FileSystemCache `.cache/`, 1h TTL, clear via `POST /api/admin/clear-cache`
- Legacy files at root (`app.js`, `data.js`, `index.html`, `style.css`) are NOT used by Flask

## Compact instructions
When compacting, preserve: current task context, SQL schema changes, and API endpoint modifications.
