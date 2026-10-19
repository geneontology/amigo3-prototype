# Build native dependencies on the build runner, never on the serving EC2 host.
FROM ghcr.io/astral-sh/uv:0.9.3 AS uv
FROM python:3.12-slim-bookworm AS build
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
ENV PATH="/root/.cargo/bin:$PATH" UV_LINK_MODE=copy
WORKDIR /opt/amigo3
COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
COPY schema/ schema/
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 libxrender1 libxext6 libexpat1 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home amigo3
COPY --from=build /opt/amigo3/.venv /opt/amigo3/.venv
ENV PATH="/opt/amigo3/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
# Fail the image build if API imports need a missing runtime library.
RUN python -c "import amigo3.api"
CMD ["amigo3", "--data", "/data", "serve", "--host", "0.0.0.0", "--solr", "http://solr:8983/solr"]
