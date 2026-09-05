# ═══════════════════════════════════════════════════════════════
# Dockerfile — Bewerbungsagent (CLI + browser-use + Streamlit-UI)
# ═══════════════════════════════════════════════════════════════
# Der Bewerbungsagent ist in erster Linie ein CLI-Werkzeug, wird aber im
# infrastruktur-deployment-Stack als Dauerläufer (Streamlit-UI) auf Port
# 8502 betrieben (siehe services.yaml, type: ui). Dieses Image bündelt
# Python, Chromium (für browser-use), Streamlit und alle Abhängigkeiten,
# damit `bewerbungsagent` überall reproduzierbar läuft UND die Web-UI ohne
# zusätzliche Argumente hochkommt.
#
#   docker build -t bewerbungsagent .
#
#   # CLI-Nutzung (Argumente werden an `bewerbungsagent` durchgereicht):
#   docker run --rm -it \
#     -e ANTHROPIC_API_KEY=... \
#     -v "$PWD/config:/app/config" \
#     -v "$PWD/unterlagen:/app/unterlagen" \
#     -v "$PWD/daten:/app/daten" \
#     bewerbungsagent suchen --was "Data Engineer" --wo Berlin
#
#   # UI-Nutzung (keine Argumente -> startet Streamlit auf $PORT, Default 8501):
#   docker run --rm -p 8501:8501 bewerbungsagent
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

# Python-Abhängigkeiten inkl. browser-Extra (browser-use + Playwright) und
# ui-Extra (Streamlit) — beide werden im Deployment-Stack benötigt.
COPY pyproject.toml README.md ./
COPY bewerbungsagent ./bewerbungsagent
RUN pip install --no-cache-dir -e ".[browser,ui]"

# Chromium für Playwright installieren (headless-fähig)
RUN python -m playwright install chromium

# Streamlit-UI-Code (Hauptseite + Unterseiten). Läuft nur, wenn der
# Container ohne Argumente gestartet wird (siehe docker-entrypoint.sh).
COPY app.py ./
COPY pages ./pages
COPY config ./config

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Konfiguration & Unterlagen werden zur Laufzeit gemountet (nicht ins Image).
# Standard-Arbeitsverzeichnis für SQLite-Daten.
ENV PYTHONUNBUFFERED=1
ENV PORT=8501

EXPOSE $PORT

ENTRYPOINT ["docker-entrypoint.sh"]
