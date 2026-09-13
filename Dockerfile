FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NLA_CONFIG="/config/config.yaml" \
    MUSIC_DIR="/music"

# Install runtime tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code and default configuration
COPY src/ ./src/
COPY config.yaml ./config.yaml

# Create default directories
RUN mkdir -p /music /config

# Use Tini for proper init and signal handling
ENTRYPOINT ["/usr/bin/tini", "--", "python", "-m", "src.main"]

# Default command: run continuous daemon with periodic scans and real-time filesystem watcher
CMD ["daemon", "--with-watch"]
