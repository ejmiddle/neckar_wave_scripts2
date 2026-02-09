FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir uv

COPY pyproject.toml /app/pyproject.toml
COPY uv.lock /app/uv.lock
# Exclude GPU-heavy deps pulled in by openai-whisper (torch/triton) for a slim image.
RUN uv sync --frozen --no-dev --no-install-package torch --no-install-package triton

COPY streamlit_app.py /app/streamlit_app.py
COPY src /app/src
COPY pages /app/pages
COPY .streamlit /app/.streamlit
COPY .env /app/.env
COPY logging.conf /app/logging.conf

# App expects app_paths and app_files.logging_config on the import path.
RUN mkdir -p /app/app_files \
    && cp /app/src/logging_config.py /app/app_files/logging_config.py \
    && touch /app/app_files/__init__.py

ENV PYTHONPATH=/app/src
VOLUME ["/app/data"]

EXPOSE 8501
CMD ["uv", "run", "streamlit", "run", "streamlit_app.py", "--server.address=0.0.0.0", "--server.port=8501"]
