FROM ghcr.io/astral-sh/uv:0.13.9 AS uv
FROM python:3.13.7-slim-bookworm@sha256:adafcc17694d715c905b4c7bebd96907a1fd5cf183395f0ebc4d3428bd22d92d

ARG LOCAL_UID=1000
ARG LOCAL_GID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONINTMAXSTRDIGITS=0 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:${PATH}" \
    HOME=/home/atcoder

COPY --from=uv /uv /uvx /usr/local/bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git build-essential cmake libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${LOCAL_GID}" atcoder \
    && useradd --uid "${LOCAL_UID}" --gid atcoder --create-home --shell /bin/bash atcoder

WORKDIR /workspace

# Keep dependencies outside the workspace bind mount.
COPY pyproject.toml uv.lock README.md /workspace/
# Reuse cached wheels, including the compiled cppyy packages, across rebuilds.
RUN --mount=type=cache,target=/tmp/uv-cache UV_CACHE_DIR=/tmp/uv-cache uv sync --frozen --no-install-project

COPY . /workspace
# uv uses the normalized distribution name for headers, while cppyy expects CPyCppyy.
RUN --mount=type=cache,target=/tmp/uv-cache UV_CACHE_DIR=/tmp/uv-cache uv sync --frozen \
    && if [ -d /opt/venv/include/site/python3.13/cpycppyy ] && [ ! -e /opt/venv/include/site/python3.13/CPyCppyy ]; then \
        ln -s cpycppyy /opt/venv/include/site/python3.13/CPyCppyy; \
    fi \
    && chown -R atcoder:atcoder /workspace /opt/venv /home/atcoder

USER atcoder
CMD ["bash"]
