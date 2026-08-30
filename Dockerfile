# ═══════════════════════════════════════════════════════════════
# Dockerfile — Bewerbungsagent (CLI + browser-use)
# ═══════════════════════════════════════════════════════════════
# Der Bewerbungsagent ist ein CLI-Werkzeug, kein Dauerläufer. Dieses Image
# bündelt Python, Chromium (für browser-use) und alle Abhängigkeiten, damit
# `bewerbungsagent` überall reproduzierbar läuft.
#
#   docker build -t bewerbungsagent .
#   docker run --rm -it \
#     -e ANTHROPIC_API_KEY=... \
#     -v "$PWD/config:/app/config" \
#     -v "$PWD/unterlagen:/app/unterlagen" \
#     -v "$PWD/daten:/app/daten" \
#     bewerbungsagent suchen --was "Data Engineer" --wo Berlin
#
# browser-use startet Chromium als root nicht mit aktivierter Sandbox; der
# Code erkennt das automatisch (chromium_sandbox=False im Container).

FROM python:3.12-slim

WORKDIR /app

# System-Abhängigkeiten: Chromium + Playwright-Browser + Browser-Treiber
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# Python-Abhängigkeiten inkl. browser-Extra (browser-use + Playwright)
COPY pyproject.toml README.md ./
COPY bewerbungsagent ./bewerbungsagent
RUN pip install --no-cache-dir -e ".[browser]"

# Chromium für Playwright installieren (headless-fähig)
RUN playwright install chromium

# Konfiguration & Unterlagen werden zur Laufzeit gemountet (nicht ins Image).
# Standard-Arbeitsverzeichnis für SQLite-Daten.
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["bewerbungsagent"]
