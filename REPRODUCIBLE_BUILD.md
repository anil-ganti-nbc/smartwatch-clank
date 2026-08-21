# Reproducible builds

Use Python 3.12 and uv 0.11.32: `uv sync --locked --all-extras && uv build`. The container has no third-party runtime dependencies and uses a digest-pinned Python base. Pass the full Git SHA as `GIT_REVISION`; CI records package artifacts, SBOM, lock digest, provenance, and image ID. Do not publish or promote.
