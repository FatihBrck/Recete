FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml ./
COPY reader/ reader/
COPY eval/ eval/
RUN pip install --no-cache-dir .
COPY config/ config/

RUN useradd --system --no-create-home --uid 10001 reader
USER reader

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8080/health', timeout=4)" || exit 1

CMD ["uvicorn", "--factory", "reader.api:create_app", "--host", "0.0.0.0", "--port", "8080"]
