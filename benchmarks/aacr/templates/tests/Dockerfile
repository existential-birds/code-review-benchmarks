FROM node:24.18.0-bookworm-slim@sha256:6f7b03f7c2c8e2e784dcf9295400527b9b1270fd37b7e9a7285cf83b6951452d
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv \
    && rm -rf /var/lib/apt/lists/*
COPY . /tests/
RUN python3 -m venv /opt/scorer \
    && /opt/scorer/bin/pip install --no-cache-dir /tests/suite \
    && npm ci --omit=dev --prefix /tests/suite/benchmarks/aacr
ENV PATH="/opt/scorer/bin:$PATH"
WORKDIR /tests/suite
