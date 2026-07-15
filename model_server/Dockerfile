# Two-tower relevance model server. PyTorch lives only in this image; the main
# backend has no ML runtime and calls this over HTTP.
FROM python:3.12-slim

# uv for dependency install.
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

WORKDIR /app

# Install runtime deps first so this layer caches unless pyproject changes.
COPY pyproject.toml ./
RUN uv sync --no-dev --no-install-project

# App code.
COPY two_tower ./two_tower
COPY server.py ./

# The trained bundle is NOT baked into the image — it is per-deployment and
# supplied at runtime (mounted, or downloaded from an artifact store) at this
# path. Nothing here reads it until the service starts.
ENV MODEL_BUNDLE_PATH=/models/model.inference.pt
ENV PYTHONPATH=/app
EXPOSE 9100

CMD ["uv", "run", "--no-dev", "--no-sync", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "9100"]
