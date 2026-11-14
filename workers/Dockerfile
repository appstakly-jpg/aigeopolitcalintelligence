# syntax=docker/dockerfile:1.7
# Python workers: migrations, ingestion pipeline, job worker, scheduler and the synthetic demo feed server.
# Build from the repository root, because the image also carries db/migrations:
#   docker build -f workers/Dockerfile -t gccintel-workers .
FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MIGRATIONS_DIR=/app/db/migrations \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

WORKDIR /app

# EXTRAS: "llm" installs the Anthropic, OpenAI and Gemini SDKs (they stay unused without API keys).
# For PDF export build with EXTRAS=llm,pdf and WITH_CHROMIUM=true (adds roughly 400 MB) and set
# PDF_EXPORT_ENABLED=true at runtime.
ARG EXTRAS=llm
ARG WITH_CHROMIUM=false

COPY workers/pyproject.toml workers/README.md /app/workers/
COPY workers/gccintel /app/workers/gccintel
# Behind a TLS-intercepting proxy, pass its CA bundle as a build secret (never stored in the image):
#   docker build --secret id=build_ca,src=/path/to/ca.pem -f workers/Dockerfile .
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -s /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    pip install "/app/workers[${EXTRAS}]" \
 && if [ "$WITH_CHROMIUM" = "true" ]; then python -m playwright install --with-deps chromium; fi

COPY db/migrations /app/db/migrations

RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin app
USER app

CMD ["gccintel", "worker"]
