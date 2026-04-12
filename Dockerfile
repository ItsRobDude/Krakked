# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.11
ARG NODE_VERSION=20
ARG POETRY_VERSION=2.2.1

# Build the UI assets with the same toolchain used in CI
FROM node:${NODE_VERSION}-bookworm AS ui-builder
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
RUN npm run build

# Build the Python wheel with Poetry
FROM python:${PYTHON_VERSION}-slim AS python-builder
ARG POETRY_VERSION
ENV POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "poetry==${POETRY_VERSION}"
COPY pyproject.toml poetry.lock ./
RUN poetry install --no-ansi --without dev --extras tui
COPY src ./src
COPY README.md ./README.md
RUN poetry build

# Final runtime image with only the packaged artifacts
FROM python:${PYTHON_VERSION}-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    KRAKKED_ENV=production \
    KRAKKED_SECRET_PW="" \
    KRAKEN_API_KEY="" \
    KRAKEN_API_SECRET="" \
    KRAKKED_UI_HOST="0.0.0.0" \
    KRAKKED_UI_PORT="8080" \
    UI_DIST_DIR="/app/ui-dist"
WORKDIR /app
COPY --from=python-builder /app/dist/*.whl /tmp/krakked.whl
RUN pip install --no-cache-dir /tmp/krakked.whl \
    && rm /tmp/krakked.whl
COPY --from=ui-builder /ui/dist ${UI_DIST_DIR}
EXPOSE 8080
ENTRYPOINT ["krakked"]
CMD ["run"]
