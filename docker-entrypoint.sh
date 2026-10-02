#!/bin/sh
# Ohne Argumente: Streamlit-UI und den taeglichen Abruf starten.
# Mit Argumenten: an die CLI durchreichen (bisheriges Verhalten, unveraendert).
set -e

if [ "$#" -eq 0 ]; then
  # Cron laeuft im selben Container wie die UI, damit der Abruf dieselbe
  # Profil-Konfiguration und die persistente SQLite-Datei verwendet. Der
  # Zeitplan ist absichtlich fest taeglich um 02:30 deutscher Zeit; lokale Installationen
  # koennen weiterhin `bewerbungsagent cron` fuer einen eigenen Zeitplan nutzen.
  # Cron wertet die Systemzeit aus, nicht nur die Prozessumgebung. Die
  # Zeitzonendatei stellt daher auch die Umstellung CET/CEST korrekt sicher.
  ln -snf /usr/share/zoneinfo/Europe/Berlin /etc/localtime
  echo "Europe/Berlin" >/etc/timezone
  cat >/etc/cron.d/bewerbungsagent <<'EOF'
SHELL=/bin/sh
PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
30 2 * * * root cd /app && bewerbungsagent pipeline --offen >> /app/daten/cron.log 2>&1
EOF
  chmod 0644 /etc/cron.d/bewerbungsagent
  cron

  exec streamlit run app.py \
    --server.port="${PORT:-8501}" \
    --server.address=0.0.0.0 \
    --server.headless=true
fi

exec bewerbungsagent "$@"
