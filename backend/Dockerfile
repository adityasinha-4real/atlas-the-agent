# ATLAS backend image. Uses Python 3.12 (broad wheel availability) even though
# the code targets 3.11+.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code.
COPY atlas ./atlas
COPY pyproject.toml ./

# Data directory for the SQLite database (mount a volume in compose).
RUN mkdir -p /app/data
ENV ATLAS_DATA_DIR=/app/data \
    ATLAS_DATABASE_URL=sqlite+aiosqlite:////app/data/atlas.db \
    ATLAS_HOST=0.0.0.0 \
    ATLAS_PORT=8000

EXPOSE 8000
CMD ["uvicorn", "atlas.main:app", "--host", "0.0.0.0", "--port", "8000"]
