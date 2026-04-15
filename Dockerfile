# syntax=docker/dockerfile:1.5
FROM python:3.12-slim

ARG STREAMLIT_APP_FILE=apps/main.py

WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv pip install --no-cache-dir uv

COPY pyproject.toml /app/pyproject.toml
COPY uv.lock /app/uv.lock
# Install all runtime dependencies from lockfile.
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

# Copy application sources and runtime assets.
COPY apps /app/apps
COPY streamlit_app.py /app/streamlit_app.py
COPY fastapi_app.py /app/fastapi_app.py
COPY src /app/src
COPY pages /app/pages
COPY api /app/api
COPY scripts /app/scripts
COPY data /app/data
COPY .streamlit /app/.streamlit
COPY logging.conf /app/logging.conf
# Bake the Google OAuth client secret into the image by design; keep the token external.
RUN mkdir -p /app/secrets/google-drive
COPY secrets/google-drive/client_secret.json /app/secrets/google-drive/client_secret.json
ENV GOOGLE_CLIENT_SECRET=/app/secrets/google-drive/client_secret.json
ENV GOOGLE_DRIVE_TOKEN_FILE=/app/secrets/google-drive/token.json

# App expects app_paths and app_files.logging_config on the import path.
RUN mkdir -p /app/app_files \
    && cp /app/src/logging_config.py /app/app_files/logging_config.py \
    && touch /app/app_files/__init__.py

ENV PYTHONPATH=/app:/app/src
ENV STREAMLIT_APP_FILE=${STREAMLIT_APP_FILE}
ENV STREAMLIT_SERVER_PORT=8501
VOLUME ["/app/data"]

EXPOSE 8501
CMD ["sh", "-c", "uv run streamlit run \"$STREAMLIT_APP_FILE\" --server.address=0.0.0.0 --server.port=${STREAMLIT_SERVER_PORT}"]
