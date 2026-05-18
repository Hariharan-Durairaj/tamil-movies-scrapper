FROM python:3.12-slim
# Install system dependencies
# chromium + chromedriver are needed for undetected-chromedriver / selenium
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    libpq-dev \
    gcc \
    xvfb \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# Copy and install Python dependencies first (layer-caching optimisation)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Expose the FastAPI port
EXPOSE 8080
WORKDIR /app
COPY backend/ ./backend/
COPY frontend/ ./frontend/
RUN mkdir -p /app/logs /app/downloads
WORKDIR /app/backend
# Remove any stale Xvfb lock from a previous run before starting.
# Without this, a container restart hits "Server is already active for display 99".
CMD ["sh", "-c", "rm -f /tmp/.X99-lock && Xvfb :99 -screen 0 1280x720x24 & sleep 1 && DISPLAY=:99 python -m uvicorn main:app --host 0.0.0.0 --port 8080 --workers 1"]
