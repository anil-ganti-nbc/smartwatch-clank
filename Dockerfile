# Smartwatch Clank — Linux AMD64 staging image. Experimental / actively
# developing — not approved for production. Only the four sources in
# config/config.yaml's production_allowlist persist to the production
# database; nothing else is promoted by this image existing.
#
# Deliberately NOT `pip install .`: PROJECT_ROOT (src/smartwatch_clank/paths.py)
# is computed as `Path(__file__).resolve().parents[2]`, which assumes the
# source-checkout layout (repo_root/src/smartwatch_clank/...). Under a real
# `pip install`, the package moves into site-packages and that same math
# resolves to somewhere under the Python installation, not the app directory
# -- the same class of path-resolution bug found and fixed on Free Game
# Tracker/Semiconductor Intelligence earlier in this migration. Rather than
# touch that path-resolution code (out of scope: no code changes beyond
# provenance), this image preserves the source-checkout layout under /app and
# runs via `python -m`, exactly like Chinese Tech Wire's flat-source pattern.
FROM python:3.12-slim-bookworm@sha256:a116514e19457bcb7af7efe9c3dd0b9b71e85b317694e7882a1c52aa15a78134

ARG GIT_REVISION=unknown
LABEL clank.id="smartwatch-clank" \
      org.opencontainers.image.revision="${GIT_REVISION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    SMARTWATCH_CLANK_SOURCE_REVISION=${GIT_REVISION}

WORKDIR /app

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin clank

# No third-party dependencies to install (pyproject.toml: dependencies = []).
COPY src ./src
COPY config ./config
COPY pyproject.toml README.md ./

RUN mkdir -p /app/var /app/data \
    && chown -R clank:clank /app

USER clank

HEALTHCHECK --interval=60s --timeout=15s --start-period=20s --retries=3 \
    CMD ["python", "-m", "smartwatch_clank.cli", "health"]

ENTRYPOINT ["python", "-m", "smartwatch_clank.cli"]
CMD ["run", "--mode", "production"]
