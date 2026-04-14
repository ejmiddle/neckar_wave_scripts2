# Neckar Wave Scripts

Automation scripts, Streamlit pages, FastAPI helpers, and data tooling for the Neckar Wave workspace.

## GitHub Actions Publish Workflow

`.github/workflows/publish.yml` builds the Docker image and pushes it to GitHub Container Registry when `main` is updated, or when the workflow is started manually.

The current workflow behavior is:

- Build with Docker Buildx.
- Log in to GHCR using the repository-scoped `GITHUB_TOKEN`.
- Publish the image as `ghcr.io/<repo-owner>/streamlit-hello:latest`.

Important local/runtime notes:

- The Google OAuth client secret is intentionally baked into the image from `secrets/google-drive/client_secret.json`.
- The OAuth token is not baked in; it is expected at `secrets/google-drive/token.json` at runtime or mounted into the container.
- Do not commit files from `secrets/`.

## Local Setup Notes

- Keep project-specific secrets in `secrets/google-drive/`.
- Keep ad-hoc exports and scratch files in `workspace/` or remove them when they are no longer needed.
- If you rebuild the image locally, make sure the client secret file exists before running `docker build`.
