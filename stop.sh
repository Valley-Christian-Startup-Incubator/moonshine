#!/usr/bin/env bash
# Stops processes started by start.sh. Installed to ~/.distill/stop.sh.

set -uo pipefail

DISTILL_HOME="${DISTILL_HOME:-$HOME/.distill}"
PID_DIR="${DISTILL_HOME}/logs"

for name in dagu web; do
	pid_file="${PID_DIR}/${name}.pid"
	if [[ -f "${pid_file}" ]]; then
		pid="$(cat "${pid_file}")"
		if kill -0 "${pid}" 2>/dev/null; then
			kill "${pid}"
			echo "Stopped ${name} (pid ${pid})"
		else
			echo "${name} not running"
		fi
		rm -f "${pid_file}"
	else
		echo "${name} not running"
	fi
done
