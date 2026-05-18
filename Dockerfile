FROM python:3.12-slim

# Install system dependencies
# git is needed to clone the repo; chromium + chromedriver for selenium
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    chromium \
    chromium-driver \
    libpq-dev \
    gcc \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

# Clone the latest code from GitHub.
# CACHE_BUST changes every build (pass via docker-compose or --build-arg)
# so Docker never reuses this layer, guaranteeing a fresh pull.
ARG REPO_URL=https://github.com/Hariharan-Durairaj/tamil-movies-scrapper.git
ARG REPO_BRANCH=main
ARG CACHE_BUST
RUN git clone --depth 1 --branch ${REPO_BRANCH} ${REPO_URL} /app

WORKDIR /app

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose the FastAPI port
EXPOSE 8080

RUN mkdir -p /app/logs /app/downloads

WORKDIR /app/backend

# Remove any stale Xvfb lock from a previous run before starting.
# Without this, a container restart hits "Server is already active for display 99".
CMD ["sh", "-c", "rm -f /tmp/.X99-lock && Xvfb :99 -screen 0 1280x720x24 & sleep 1 && DISPLAY=:99 python -m uvicorn main:app --host 0.0.0.0 --port 8080 --workers 1"]
