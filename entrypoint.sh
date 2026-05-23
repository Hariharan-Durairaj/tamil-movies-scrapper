#!/bin/bash
# =============================================================================
# entrypoint.sh — start Xvfb then launch the FastAPI backend
# =============================================================================
#
# Why Xvfb?
# ----------
# The DomainFinder (Settings → Domain → "1TamilMV Domain Tracker") uses headful
# Chrome (no --headless flag) to search DuckDuckGo / Bing / Brave for the
# current 1tamilmv domain.  Without a real display, Chrome has no rendering
# context and Intersection-Observer / visibility-based lazy-loading never
# fires, so search result links are never injected into the DOM.
#
# Xvfb creates a virtual framebuffer display at :99 (1920×1080, 24-bit colour).
# Chrome renders into it exactly as it would on a real desktop monitor,
# which triggers all JS-based content loading and makes result links visible
# to Selenium.
#
# Using a dedicated entrypoint script (rather than an inline sh -c string in
# the Dockerfile CMD) means:
#   • Xvfb's PID is tracked so it can be killed cleanly on SIGTERM / SIGINT.
#   • The stale X-lock cleanup runs before every start, preventing the
#     "Server is already active for display :99" error on container restart.
# =============================================================================

set -e

DISPLAY_NUM=99
DISPLAY=":${DISPLAY_NUM}"
export DISPLAY

# ---------------------------------------------------------------------------
# 1. Remove any stale Xvfb lock left over from a previous (crashed) run.
#    Without this, a container restart hits:
#      "Fatal server error: Server is already active for display :99"
# ---------------------------------------------------------------------------
rm -f "/tmp/.X${DISPLAY_NUM}-lock"
rm -f "/tmp/.X11-unix/X${DISPLAY_NUM}"

# ---------------------------------------------------------------------------
# 2. Start Xvfb on a 1920×1080 24-bit virtual display.
#      -ac            : disable access control (allow any local connection)
#      +extension GLX : enable GLX so Chrome's GPU process starts cleanly
#      +render        : enable the RENDER extension
#      -noreset       : keep the server running even if all clients disconnect
# ---------------------------------------------------------------------------
echo "[ENTRYPOINT] Starting Xvfb on display ${DISPLAY}..."
Xvfb "${DISPLAY}" \
    -screen 0 1920x1080x24 \
    -ac \
    +extension GLX \
    +render \
    -noreset &
XVFB_PID=$!

# ---------------------------------------------------------------------------
# 3. Give Xvfb a moment to initialise before Chrome tries to connect.
# ---------------------------------------------------------------------------
sleep 2

# Verify Xvfb is still running
if ! kill -0 "${XVFB_PID}" 2>/dev/null; then
    echo "[ENTRYPOINT] ERROR: Xvfb failed to start" >&2
    exit 1
fi
echo "[ENTRYPOINT] Xvfb running (PID ${XVFB_PID}) on ${DISPLAY}"

# ---------------------------------------------------------------------------
# 4. Start D-Bus session bus (some sites / Chrome features need it).
#    We ignore failures here — it is a best-effort nicety.
# ---------------------------------------------------------------------------
if command -v dbus-daemon >/dev/null 2>&1; then
    dbus-daemon --system --fork 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
# 5. Trap SIGTERM / SIGINT so docker stop cleans up Xvfb gracefully.
# ---------------------------------------------------------------------------
_cleanup() {
    echo "[ENTRYPOINT] Received shutdown signal — stopping Xvfb (PID ${XVFB_PID})..."
    kill "${XVFB_PID}" 2>/dev/null || true
    exit 0
}
trap _cleanup TERM INT

# ---------------------------------------------------------------------------
# 6. Launch the FastAPI backend.
#    DISPLAY is already exported above so every subprocess (Chrome included)
#    inherits it automatically.
# ---------------------------------------------------------------------------
echo "[ENTRYPOINT] Starting FastAPI backend..."
exec python -m uvicorn main:app --host 0.0.0.0 --port 8080 --workers 1
