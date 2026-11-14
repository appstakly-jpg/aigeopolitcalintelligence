#!/usr/bin/env bash
# Create .env from .env.example with freshly generated secrets.
#   ./scripts/init-env.sh           production-style defaults (demo off, secure cookies)
#   ./scripts/init-env.sh --demo    local demo: synthetic demo organisation, mail catcher, plain-http cookies
# Refuses to overwrite an existing .env.
set -euo pipefail
cd "$(dirname "$0")/.."

demo=false
[ "${1:-}" = "--demo" ] && demo=true
if [ -e .env ]; then
  echo ".env already exists; remove it first if you want a new one." >&2
  exit 1
fi

rand_hex() { # $1 bytes
  if command -v openssl > /dev/null; then openssl rand -hex "$1"; else head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'; fi
}
rand_b64() { # $1 bytes
  if command -v openssl > /dev/null; then openssl rand -base64 "$1"; else head -c "$1" /dev/urandom | base64; fi
}

set_var() { # key value: replace the KEY= line in .env (values here never contain '|')
  sed -i.bak "s|^$1=.*|$1=$2|" .env && rm -f .env.bak
}

cp .env.example .env
chmod 600 .env
set_var POSTGRES_PASSWORD "$(rand_hex 24)"
set_var SESSION_SECRET "$(rand_hex 32)"
set_var APP_ENCRYPTION_KEY "$(rand_b64 32 | tr -d '\n')"

if [ "$demo" = true ]; then
  demo_password="$(rand_hex 9)"
  set_var DEMO_MODE true
  set_var DEMO_PASSWORD "$demo_password"
  set_var ALLOW_PRIVATE_NETWORK_FETCH true
  set_var SECURE_COOKIES false
  set_var SMTP_HOST mailpit
  set_var SMTP_PORT 1025
  set_var SMTP_STARTTLS false
  set_var SMTP_FROM alerts@gccintel.local
  echo "Wrote .env for the local demo."
  echo "Start it:   docker compose --profile demo up -d --build"
  echo "Sign in at  http://localhost:3000 as admin@demo.example (or analyst@, reviewer@, client@)"
  echo "Password:   $demo_password   (also in .env as DEMO_PASSWORD)"
  echo "Alert email is caught at http://localhost:8025"
else
  echo "Wrote .env with generated secrets. Review APP_BASE_URL, SMTP and API keys, then:"
  echo "  docker compose up -d --build"
  echo "  docker compose run --rm migrate gccintel tenant-create --slug acme --name 'Acme' --plan team --admin-email you@example.com"
fi
