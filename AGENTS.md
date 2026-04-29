# Neckarwave Scripts Howto

## Fast mental model

This repo is a collection of accounting and data-processing scripts with a Streamlit front end, a sevDesk API layer, and a set of file-based caches and exports.

The accounting app starts in `pages/Accounting.py`, which calls `src/streamlit_apps/accounting_app.py`. That file routes into the page wrappers under `pages/accounting_*.py`, which in turn call `src/accounting/page.py` and the tab implementations under `src/accounting/ui/`.

sevDesk requests live in `src/sevdesk/api.py`. Formatting and table shaping for the UI live in `src/accounting/sevdesk_browse.py` and `src/accounting/ui/displays.py`.

## Common workflow

1. Install dependencies with `uv`.
2. Set `SEVDESK_KEY` in the environment or `.env`.
3. Run the Streamlit app and use the Accounting pages.
4. Inspect cached API responses in `data/sevdesk/cache/` when debugging table output.
5. Add or update tests with `pytest`.

## Useful commands

```bash
uv run python -m pytest
uv run python -m unittest test_sevdesk_browse.py
uv run streamlit run pages/Accounting.py
```

## Repo layout

- `src/accounting/` contains the business logic for invoices, vouchers, Amazon workflows, payments, and master data.
- `src/sevdesk/` contains API wrappers, payload builders, and validation helpers for sevDesk.
- `pages/` contains the Streamlit entry pages.
- `data/sevdesk/` is organized by lifecycle: `cache/` for disposable responses, `exports/` for re-creatable snapshots, `generated/` for derived payloads, `inputs/` for source files, and `state/` for user-edited persistent data.
- `config/sevdesk/` contains static sevDesk templates and example payloads.

## Things to check first when something looks wrong

- Whether the raw sevDesk payload in `data/sevdesk/cache/` has the field you expect.
- Whether the formatter in `src/accounting/sevdesk_browse.py` or `src/accounting/ui/displays.py` is dropping the value.
- Whether a page is reading from `st.session_state` cache instead of refetching live data.
- Whether the token is missing, which usually means the UI can only show stored data.
