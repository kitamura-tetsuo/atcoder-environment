FROM ghcr.io/astral-sh/uv:0.13.9 AS uv
FROM python:3.12-slim-bookworm

ARG LOCAL_UID=1000
ARG LOCAL_GID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:${PATH}" \
    HOME=/home/atcoder

COPY --from=uv /uv /uvx /usr/local/bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${LOCAL_GID}" atcoder \
    && useradd --uid "${LOCAL_UID}" --gid atcoder --create-home --shell /bin/bash atcoder

WORKDIR /workspace

# Keep dependencies outside the workspace bind mount.
COPY pyproject.toml uv.lock README.md /workspace/
RUN uv sync --frozen --no-install-project

COPY . /workspace
RUN uv sync --frozen \
    && chown -R atcoder:atcoder /workspace /opt/venv /home/atcoder

USER atcoder
CMD ["bash"]
