FROM python:3.12-slim

WORKDIR /srv/trading-system

RUN pip install --no-cache-dir uv

COPY pyproject.toml ./
COPY app ./app
COPY scripts ./scripts

RUN uv pip install --system --no-cache .

EXPOSE 8000

CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
