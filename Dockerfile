FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .
COPY templates/ templates/
COPY static/ static/
COPY entrypoint.sh /entrypoint.sh

# Create data directory for persistent config
RUN mkdir -p /app/data

# Non-root user for security
RUN chmod +x /entrypoint.sh && \
    useradd -M -d /tmp -s /sbin/nologin appuser && \
    chown -R appuser:appuser /app

# Environment defaults
ENV APP_PORT=5000 \
    DEBUG=false \
    DATA_DIR=/app/data \
    HOME=/tmp

# Expose port
EXPOSE $APP_PORT

ENTRYPOINT ["/entrypoint.sh"]