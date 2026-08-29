#!/usr/bin/env bash
# Manually start Dagu + the SvelteKit web app as background processes.
# Installed to ~/.distill/start.sh by setup.sh. Prefer the launchd services
# registered by setup.sh for persistence — use this
# script for a manual restart without touching launchd.

set -euo pipefail

DISTILL_HOME="${DISTILL_HOME:-$HOME/.distill}"
# The env file is deliberately data, not shell code. Reading it this way keeps
# passwords containing spaces or shell metacharacters intact.
while IFS='=' read -r key value; do
	case "${key}" in
		DISTILL_HOME|DAGU_BASE_URL|DAGU_PORT|DAGU_USER|DAGU_PASSWORD|ADMIN_PASSWORD|WEB_PASSWORD|PORT|BODY_SIZE_LIMIT|DIAGNOSTIC_AGENT|DIAGNOSTIC_MODEL|DIAGNOSTIC_OLLAMA_URL)
			export "${key}=${value}"
			;;
	esac
done < "${DISTILL_HOME}/env"
export PATH="/opt/homebrew/bin:/usr/local/bin:${HOME}/.local/bin:${HOME}/.cargo/bin:${HOME}/.npm-global/bin:${PATH}"

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
		ADMIN_PASSWORD="${ADMIN_PASSWORD}" WEB_PASSWORD="${WEB_PASSWORD}" \
		nohup node build/index.js \
		> "${PID_DIR}/web.out.log" 2> "${PID_DIR}/web.err.log" &)
	sleep 1
	pgrep -f "node build/index.js" | tail -1 > "${PID_DIR}/web.pid" || true
	echo "Started web app on port ${PORT}"
fi
