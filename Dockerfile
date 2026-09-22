FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NLA_CONFIG="/config/config.yaml" \
    NLA_CACHE_DB_PATH="/data/lyrics_cache.db" \
    MUSIC_DIR="/music" \
    LD_PRELOAD="/usr/lib/libjemalloc.so.2"

# Install runtime tools and jemalloc memory allocator
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    tini \
    libjemalloc2 \
    && ln -s "$(find /usr/lib -name 'libjemalloc.so.2' | head -n 1)" /usr/lib/libjemalloc.so.2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code and example configuration
COPY src/ ./src/
COPY config.example.yaml ./config.example.yaml
COPY config.example.yaml ./config.yaml

# Create default directories and declare volumes
RUN mkdir -p /music /config /data
VOLUME ["/music", "/config", "/data"]

# Expose Web UI port
EXPOSE 8080

# Healthcheck testing application runtime and database connectivity
HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
    CMD python -m src.main cache --stats > /dev/null 2>&1 || exit 1

# Use Tini for proper init and signal handling
ENTRYPOINT ["/usr/bin/tini", "--", "python", "-m", "src.main"]

# Default command: run continuous daemon with periodic scans and real-time filesystem watcher
CMD ["daemon", "--with-watch"]
