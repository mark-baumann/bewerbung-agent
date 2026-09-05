#!/bin/sh
# Ohne Argumente: Streamlit-UI starten (Dauerläufer-Modus im Deployment-Stack).
# Mit Argumenten: an die CLI durchreichen (bisheriges Verhalten, unverändert).
set -e

if [ "$#" -eq 0 ]; then
  exec streamlit run app.py \
    --server.port="${PORT:-8501}" \
    --server.address=0.0.0.0 \
    --server.headless=true
fi

exec bewerbungsagent "$@"
