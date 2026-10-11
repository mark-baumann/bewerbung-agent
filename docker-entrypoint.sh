#!/bin/sh
# Ohne Argumente: Streamlit-UI und den Zeitplan (Cron-Tick) starten.
# Mit Argumenten: an die CLI durchreichen (bisheriges Verhalten, unveraendert).
set -e

if [ "$#" -eq 0 ]; then
  # Cron laeuft im selben Container wie die UI, damit Automatik und Versand
  # dieselbe Profil-Konfiguration und die persistente SQLite-Datei verwenden.
  # Cron wertet die Systemzeit aus, nicht nur die Prozessumgebung. Die
  # Zeitzonendatei stellt daher auch die Umstellung CET/CEST korrekt sicher.
  ln -snf /usr/share/zoneinfo/Europe/Berlin /etc/localtime
  echo "Europe/Berlin" >/etc/timezone

  # Cron startet Jobs mit leerer Umgebung - ohne diese Datei faenden Tick und
  # Versand weder LLM- noch SMTP-Zugangsdaten. Sie liegt bewusst nicht im
  # Daten-Volume und ist nur fuer root lesbar.
  python - <<'PY'
import os, shlex
zeilen = [f"export {k}={shlex.quote(v)}" for k, v in sorted(os.environ.items())
          if k.isidentifier() and k not in ("HOME", "PWD", "SHLVL", "_")]
pfad = "/etc/bewerbungsagent.env"
with open(os.open(pfad, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
    f.write("\n".join(zeilen) + "\n")
PY

  # Alle 5 Minuten ein Tick: Er startet den Automatiklauf, sobald der im
  # Profil eingestellte Zeitplan faellig ist (Standard taeglich 06:00), und
  # verschickt freigegebene Bewerbungen. Zeitplan-Aenderungen in der UI
  # greifen damit sofort, ohne Neustart.
  cat >/etc/cron.d/bewerbungsagent <<'EOF2'
SHELL=/bin/sh
PATH=/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin
*/5 * * * * root . /etc/bewerbungsagent.env; cd /app && bewerbungsagent tick >> /app/daten/cron.log 2>&1
EOF2
  chmod 0644 /etc/cron.d/bewerbungsagent
  cron

  exec streamlit run app.py \
    --server.port="${PORT:-8501}" \
    --server.address=0.0.0.0 \
    --server.headless=true
fi

exec bewerbungsagent "$@"
