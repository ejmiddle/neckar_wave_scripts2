# Mittwald API Access and Debugging Notes

Date: 2026-04-15

## What We Have

- The repo uses `MITTWALD_API_TOKEN` for authenticated Mittwald API access.
- The checker script reads the token from the environment and also loads `.env` when present.
- The current repo tasks are:
  - `task dev_mittwald_suedseite_containers_status`
  - `task dev_mittwald_suedseite_containers_logs`
  - `task dev_mittwald_suedseite_containers_unhealthy`

## Script Behavior

- The main checker script is `scripts/check_mittwald_project_containers.py`.
- It resolves the Mittwald project by searching for `suedseite`.
- It lists the project services and prints:
  - service name
  - status
  - short ID
  - status timestamp
  - message
- It can also fetch service logs through the Mittwald API.
- It supports:
  - `--show-logs`
  - `--show-logs-for-all`
  - `--only-unhealthy`
  - `--log-tail`

## Mittwald Facts We Confirmed

- The project `suedseite` exists and resolves to short ID `p-bab052`.
- The project status is `ready`.
- The services we saw are:
  - `testcontainer`
  - `fastapi`
  - `accounting`
- Live API output showed `accounting` as `running` with message `Container is ready`.

## Ingress / Domain Mapping

- The hostname `nwacc.p-bab052.project.space` exists in Mittwald ingress configuration.
- It points to the `accounting` container.
- The ingress target is `8501/tcp`.

## Log Evidence

- The `accounting` container log shows Streamlit started successfully.
- The startup log includes:
  - `You can now view your Streamlit app in your browser.`
  - `Local URL: http://localhost:8080`
  - `External URL: http://45.144.184.45:8080`
- There were no obvious crash traces in the captured log.

## Root Cause Found

- The ingress expects the app on `8501/tcp`.
- The container log showed Streamlit starting on port `8080`.
- That mismatch explains the `502 Bad Gateway` on `https://nwacc.p-bab052.project.space`.

## Repo Changes Made

- `Dockerfile` now defaults `STREAMLIT_SERVER_PORT=8501` and `EXPOSE 8501`.
- The local `Taskfile.yaml` run commands still map host ports for local dev.

## What This Means

- The current evidence points to a deployment port mismatch, not a container crash.
- The fix is to rebuild and redeploy the accounting image so the Mittwald runtime listens on `8501`.

## Remaining Useful Checks

- Re-run the Mittwald checker after redeploy to confirm:
  - `accounting` is still `running`
  - the ingress target is still `8501/tcp`
  - the logs show Streamlit starting on `8501`
- If 502 remains after redeploy, the next likely causes are:
  - wrong ingress target
  - stale deployment image
  - app startup failure after the first request
