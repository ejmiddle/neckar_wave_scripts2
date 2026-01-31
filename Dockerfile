FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir uv

COPY pyproject.toml /app/pyproject.toml
COPY uv.lock /app/uv.lock
RUN uv sync --frozen

COPY testapp.py /app/testapp.py
COPY app_files /app/app_files
COPY .streamlit /app/.streamlit
COPY .env /app/.env

EXPOSE 8501
CMD ["uv", "run", "streamlit", "run", "testapp.py", "--server.address=0.0.0.0", "--server.port=8501"]
