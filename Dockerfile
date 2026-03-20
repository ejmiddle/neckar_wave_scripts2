# syntax=docker/dockerfile:1.5
FROM python:3.12-slim

WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv pip install --no-cache-dir uv

COPY pyproject.toml /app/pyproject.toml
COPY uv.lock /app/uv.lock
# Install all runtime dependencies from lockfile.
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

# Copy application sources and runtime assets.
COPY streamlit_app.py /app/streamlit_app.py
COPY fastapi_app.py /app/fastapi_app.py
COPY src /app/src
COPY pages /app/pages
COPY api /app/api
COPY scripts /app/scripts
COPY data /app/data
COPY .streamlit /app/.streamlit
COPY .env /app/.env
COPY logging.conf /app/logging.conf
COPY client_secret*.json /app/
COPY token.json /app/token.json
RUN sh -c 'for f in /app/credentials.json /app/client_secret*.json; do if [ -f \"$f\" ]; then ln -sf \"$f\" /app/client_secret.json; break; fi; done'
ENV GOOGLE_CLIENT_SECRET=/app/client_secret.json
ENV GOOGLE_DRIVE_TOKEN_FILE=/app/token.json

# App expects app_paths and app_files.logging_config on the import path.
RUN mkdir -p /app/app_files \
    && cp /app/src/logging_config.py /app/app_files/logging_config.py \
    && touch /app/app_files/__init__.py

ENV PYTHONPATH=/app/src
VOLUME ["/app/data"]

EXPOSE 8501
CMD ["uv", "run", "streamlit", "run", "streamlit_app.py", "--server.address=0.0.0.0", "--server.port=8501"]
