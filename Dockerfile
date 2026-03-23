# ─── Build stage ──────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /build
COPY pyproject.toml uv.lock* README.md* ./
COPY c3/ c3/

RUN uv pip install --system --no-cache .

# ─── Runtime stage ────────────────────────────────────────────────────────────
FROM python:3.12-slim

# Node.js for Baileys bridge
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates git \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/c3-py /usr/local/bin/c3-py

RUN uv pip install --system --no-cache "mcp-approval-proxy @ git+https://github.com/vaddisrinivas/mcp-approval-proxy.git@master"

COPY c3/ /app/c3/
COPY package.json package-lock.json* /app/
WORKDIR /app
RUN npm install --production --ignore-scripts --silent

# Non-root user
RUN useradd -m -u 1001 -s /bin/bash c3
RUN mkdir -p /plugin/sessions /home/c3/.claude/projects && chown -R c3:c3 /plugin /home/c3/.claude
USER c3

# Install Claude Code native binary
RUN curl -fsSL https://claude.ai/install.sh | bash
ENV PATH="/home/c3/.local/bin:${PATH}"

VOLUME ["/plugin", "/home/c3/.claude"]
ENV PYTHONUNBUFFERED=1

COPY entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
