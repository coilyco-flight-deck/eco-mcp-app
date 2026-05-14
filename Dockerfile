FROM python:3.13

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock /app/
RUN uv sync --frozen --no-dev --no-install-project

COPY . /app
RUN uv sync --frozen --no-dev

ENV PORT=4000
EXPOSE $PORT

CMD ["sh", "-c", ".venv/bin/uvicorn eco_mcp_app.http_app:app --host 0.0.0.0 --port $PORT"]
