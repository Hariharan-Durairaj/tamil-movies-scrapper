FROM python:3.12-slim

# ---------------------------------------------------------------------------
# System dependencies
# ---------------------------------------------------------------------------
# Core tools + PostgreSQL client libs
# Chromium + its driver (used by undetected-chromedriver for IMDB / DomainFinder)
# Xvfb  — X Virtual Frame Buffer: gives headful Chrome a real display context
#          so that Intersection-Observer / visibility-based lazy-loading fires.
# All extra libs listed below are required at runtime by Chromium inside a
# container (GTK3, NSS, CUPS, DRM, GBM, ATK, etc.).  Missing any one of them
# causes Chrome to crash silently with exit-code 127 / 1.
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    chromium \
    chromium-driver \
    libpq-dev \
    gcc \
    # ── Xvfb and X11 helpers ────────────────────────────────────────────
    xvfb \
    x11-utils \
    dbus-x11 \
    # ── Runtime libs required by Chromium in a headless container ───────
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Clone latest code
# CACHE_BUST changes every build (pass via docker-compose or --build-arg)
# so Docker never reuses this layer, guaranteeing a fresh pull.
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Entrypoint script
# ---------------------------------------------------------------------------
# We use a proper entrypoint script rather than an inline sh -c string so
# that signal handling (SIGTERM / SIGINT from docker stop) works correctly
# and the Xvfb PID is tracked and cleaned up on exit.
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

WORKDIR /app/backend

ENTRYPOINT ["/entrypoint.sh"]
