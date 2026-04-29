# Neckar Wave Scripts

Automation scripts, Streamlit pages, FastAPI helpers, and data tooling for the Neckar Wave workspace.

## GitHub Actions Publish Workflow

`.github/workflows/publish.yml` builds the Docker image and pushes it to GitHub Container Registry when `main` is updated, or when the workflow is started manually.

The current workflow behavior is:

- Build with Docker Buildx.
- Log in to GHCR using the repository-scoped `GITHUB_TOKEN`.
- Publish the main Streamlit image as `ghcr.io/<repo-owner>/neckarwave-scripts-main:latest`.

Related image names used by the repo:

- `ghcr.io/<repo-owner>/neckarwave-scripts-main:latest` for the main Streamlit app.
- `ghcr.io/<repo-owner>/neckarwave-scripts-accounting:latest` for the accounting Streamlit app.
- `ghcr.io/<repo-owner>/neckarwave-scripts-fastapi:latest` for the FastAPI backend.

## Secrets

The Google Drive auth flow uses two local files:

- `secrets/google-drive/client_secret.json`
- `secrets/google-drive/token.json`

How they are used:

- `client_secret.json` is the Google OAuth client secret. It is intentionally copied into the Docker image by [`Dockerfile`](/Users/andreasschmidt/CodingProjects/neckarwave_scripts/Dockerfile).
- `token.json` is the OAuth token cache. It is runtime-only and should stay local or be mounted into the container.
- Both paths are ignored by git through [`.gitignore`](/Users/andreasschmidt/CodingProjects/neckarwave_scripts/.gitignore).

Behavior and overrides:

- The app looks for `GOOGLE_CLIENT_SECRET` and `GOOGLE_DRIVE_TOKEN_FILE` first.
- If those are not set, it falls back to `secrets/google-drive/client_secret.json` and `secrets/google-drive/token.json`.
- A service account can still be supplied separately via `GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON` in environment variables or `st.secrets`.

Do not commit any files under `secrets/`. If you need to regenerate the OAuth token, delete `secrets/google-drive/token.json` and run the Drive auth flow again.

## Deployment Secrets

There is a minimal encrypted deploy-config setup for Mittwald deployments.

Use [`deployment-secrets.md`](/Users/andreasschmidt/CodingProjects/neckarwave_scripts/deployment-secrets.md) for the workflow and copy-paste commands.

## Local Setup Notes

- Keep project-specific secrets in `secrets/google-drive/`.
- Keep ad-hoc exports and scratch files in `workspace/` or remove them when they are no longer needed.
- If you rebuild the image locally, make sure the client secret file exists before running `docker build`.
