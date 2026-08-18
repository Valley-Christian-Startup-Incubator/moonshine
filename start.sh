#!/usr/bin/env bash
# Manually start Dagu + the SvelteKit web app as background processes.
# Installed to ~/.distill/start.sh by setup.sh. Prefer `launchctl load` for
# boot-persistent services (setup.sh already registers those) — use this
# script for a manual restart without touching launchd.

set -euo pipefail

DISTILL_HOME="${DISTILL_HOME:-$HOME/.distill}"
source "${DISTILL_HOME}/env"

PID_DIR="${DISTILL_HOME}/logs"
mkdir -p "${PID_DIR}"

if [[ -f "${PID_DIR}/dagu.pid" ]] && kill -0 "$(cat "${PID_DIR}/dagu.pid")" 2>/dev/null; then
	echo "Dagu already running (pid $(cat "${PID_DIR}/dagu.pid"))"
else
	nohup dagu start-all --config "${DISTILL_HOME}/dagu/config.yaml" \
		> "${PID_DIR}/dagu.out.log" 2> "${PID_DIR}/dagu.err.log" &
	echo $! > "${PID_DIR}/dagu.pid"
	echo "Started Dagu (pid $!)"
fi

if [[ -f "${PID_DIR}/web.pid" ]] && kill -0 "$(cat "${PID_DIR}/web.pid")" 2>/dev/null; then
	echo "Web app already running (pid $(cat "${PID_DIR}/web.pid"))"
else
	(cd "${DISTILL_HOME}/web" && \
		DISTILL_HOME="${DISTILL_HOME}" PORT="${PORT}" BODY_SIZE_LIMIT=Infinity \
		DAGU_BASE_URL="${DAGU_BASE_URL}" DAGU_USER="${DAGU_USER}" DAGU_PASSWORD="${DAGU_PASSWORD}" \
		ADMIN_PASSWORD="${ADMIN_PASSWORD}" \
		nohup node build/index.js \
		> "${PID_DIR}/web.out.log" 2> "${PID_DIR}/web.err.log" &)
	sleep 1
	pgrep -f "node build/index.js" | tail -1 > "${PID_DIR}/web.pid" || true
	echo "Started web app on port ${PORT}"
fi
