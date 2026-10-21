#!/usr/bin/env bash
# Bootstrap the distillation job scheduler on a Mac Studio (Apple Silicon).
# Installs Dagu, mlx-lm, builds the SvelteKit app, and wires up background
# services. Safe to re-run — every step is idempotent.
#
# Runs entirely as your own user: everything lands under $HOME (binaries in
# ~/.local/bin, app + data in ~/.distill, LaunchAgents in ~/Library), so no
# sudo or admin rights are needed at any point.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DISTILL_HOME="${DISTILL_HOME:-$HOME/.distill}"
VENV_DIR="${VENV_DIR:-$HOME/.distill-venv}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
DAGU_VERSION="${DAGU_VERSION:-1.17.4}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
PYDANTIC_AI_VERSION="${PYDANTIC_AI_VERSION:-2.35.3}"

# Common install locations for tools that aren't always on the default
# launchd PATH (Homebrew, uv, npm-global, the diagnostic agent CLIs).
ORIG_PATH="${PATH}"
EXTRA_PATH="${BIN_DIR}:/opt/homebrew/bin:/usr/local/bin:${HOME}/.local/bin:${HOME}/.cargo/bin:${HOME}/.npm-global/bin"
export PATH="${EXTRA_PATH}:${PATH}"

# Credentials. Re-running setup.sh must NOT rotate these — students and
# operators already have the passwords from the first run. Precedence:
# explicit environment variable > value already in ${ENV_FILE} > freshly
# generated (first install only).
ENV_FILE="${DISTILL_HOME}/env"

prev_env() {
	[[ -f "${ENV_FILE}" ]] || return 0
	sed -n "s/^$1=//p" "${ENV_FILE}" | tail -1
}

WEB_PORT="${WEB_PORT:-$(prev_env PORT)}"
WEB_PORT="${WEB_PORT:-3000}"
DAGU_PORT="${DAGU_PORT:-$(prev_env DAGU_PORT)}"
DAGU_PORT="${DAGU_PORT:-8081}"

DAGU_USER="${DAGU_USER:-$(prev_env DAGU_USER)}"
DAGU_USER="${DAGU_USER:-admin}"

DAGU_PASSWORD="${DAGU_PASSWORD:-$(prev_env DAGU_PASSWORD)}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-$(prev_env ADMIN_PASSWORD)}"
WEB_PASSWORD="${WEB_PASSWORD:-$(prev_env WEB_PASSWORD)}"

if [[ -n "${DAGU_PASSWORD}" && -n "${ADMIN_PASSWORD}" ]]; then
	CREDS_NOTE="(unchanged from your previous run)"
else
	CREDS_NOTE="(newly generated — save them)"
fi
DAGU_PASSWORD="${DAGU_PASSWORD:-$(openssl rand -hex 8)}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-$(openssl rand -hex 8)}"
WEB_PASSWORD="${WEB_PASSWORD:-${ADMIN_PASSWORD}}"

# Same treatment: an unset DIAGNOSTIC_AGENT shouldn't wipe a previously
# configured one.
DIAGNOSTIC_AGENT="${DIAGNOSTIC_AGENT:-$(prev_env DIAGNOSTIC_AGENT)}"
DIAGNOSTIC_MODEL="${DIAGNOSTIC_MODEL:-$(prev_env DIAGNOSTIC_MODEL)}"
DIAGNOSTIC_MODEL="${DIAGNOSTIC_MODEL:-qwen3.8:27b-mlx}"
DIAGNOSTIC_OLLAMA_URL="${DIAGNOSTIC_OLLAMA_URL:-$(prev_env DIAGNOSTIC_OLLAMA_URL)}"
DIAGNOSTIC_OLLAMA_URL="${DIAGNOSTIC_OLLAMA_URL:-http://127.0.0.1:11434}"
OPENAI_API_KEY="${OPENAI_API_KEY:-$(prev_env OPENAI_API_KEY)}"
TOPIC_CHAT_MODEL="${TOPIC_CHAT_MODEL:-$(prev_env TOPIC_CHAT_MODEL)}"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$1" >&2; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$1" >&2; exit 1; }

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
	die "This script targets Apple Silicon Macs (Darwin/arm64)."
fi

# Homebrew isn't required — it's only used as a fallback to install Node if
# it's missing, and Homebrew itself installs into a user-writable prefix.

# ---------------------------------------------------------------------------
log "Creating directory structure at ${DISTILL_HOME}"
# ---------------------------------------------------------------------------
mkdir -p \
	"${DISTILL_HOME}/jobs" \
	"${DISTILL_HOME}/results" \
	"${DISTILL_HOME}/dagu/dags" \
	"${DISTILL_HOME}/dagu/data/logs" \
	"${DISTILL_HOME}/web" \
	"${DISTILL_HOME}/scripts" \
	"${DISTILL_HOME}/logs"

# ---------------------------------------------------------------------------
log "Setting up Python ${PYTHON_VERSION} venv at ${VENV_DIR}"
# ---------------------------------------------------------------------------
command -v uv >/dev/null 2>&1 || die "Install verified uv 0.13.4+ before running setup (https://github.com/astral-sh/uv/releases)."
uv_version="$(uv --version | awk '{print $2}')"
IFS=. read -r uv_major uv_minor uv_patch <<<"${uv_version}"
if [[ "${uv_major}" -eq 0 ]] && { [[ "${uv_minor}" -lt 13 ]] || { [[ "${uv_minor}" -eq 13 ]] && [[ "${uv_patch}" -lt 4 ]]; }; }; then
	die "uv 0.13.4+ is required for the October 2026 certificate fix."
fi
uv python install "${PYTHON_VERSION}"
if [[ ! -d "${VENV_DIR}" ]]; then
	uv venv "${VENV_DIR}" --python "${PYTHON_VERSION}"
fi
uv pip install --python "${VENV_DIR}/bin/python" pip mlx-lm \
	"pydantic-ai-slim[openai]==${PYDANTIC_AI_VERSION}" -r "${SCRIPT_DIR}/requirements-security.txt"
uv pip install --python "${VENV_DIR}/bin/python" mlx-tune \
	|| warn "mlx-tune unavailable; the mlx-lm LoRA trainer remains available."

CA_BUNDLE="$("${VENV_DIR}/bin/python" -c 'import certifi; print(certifi.where())')"
mkdir -p "${DISTILL_HOME}/empty-ca-directory"
export SSL_CERT_FILE="${CA_BUNDLE}"
export SSL_CERT_DIR="${DISTILL_HOME}/empty-ca-directory"
export HF_HUB_DISABLE_XET=1

# ---------------------------------------------------------------------------
log "Installing Dagu ${DAGU_VERSION}"
# ---------------------------------------------------------------------------
DAGU_TARBALL="dagu_${DAGU_VERSION}_go1.27.3_darwin_arm64.tar.gz"
DAGU_URL="https://github.com/Valley-Christian-Startup-Incubator/moonshine/releases/download/security-2026-10-21/${DAGU_TARBALL}"
DAGU_ARCHIVE_SHA256="2ea3cac626051e9945909bb660ce3974e158db7ff1cfce807edb7240b8aa21d0"
DAGU_BINARY_SHA256="7030d07238a26080a808a6fcd997cd126eca785f1bb72b08329fb8aedc753f44"
if [[ -x "${BIN_DIR}/dagu" ]] && [[ "$(shasum -a 256 "${BIN_DIR}/dagu" | awk '{print $1}')" == "${DAGU_BINARY_SHA256}" ]]; then
	log "Verified Dagu ${DAGU_VERSION} / Go 1.27.3 already installed."
else
	TMP_DIR="$(mktemp -d)"
	"${VENV_DIR}/bin/python" "${SCRIPT_DIR}/scripts/download_verified.py" \
		"${DAGU_URL}" "${DAGU_ARCHIVE_SHA256}" "${TMP_DIR}/${DAGU_TARBALL}"
	tar -xzf "${TMP_DIR}/${DAGU_TARBALL}" -C "${TMP_DIR}"
	[[ "$(shasum -a 256 "${TMP_DIR}/dagu" | awk '{print $1}')" == "${DAGU_BINARY_SHA256}" ]] \
		|| die "Dagu executable checksum mismatch."
	mkdir -p "${BIN_DIR}" "${DISTILL_HOME}/security-backups"
	if [[ -f "${BIN_DIR}/dagu" && ! -f "${DISTILL_HOME}/security-backups/dagu-before-20261021" ]]; then
		cp -p "${BIN_DIR}/dagu" "${DISTILL_HOME}/security-backups/dagu-before-20261021"
	fi
	install -m 755 "${TMP_DIR}/dagu" "${BIN_DIR}/dagu"
	rm -rf "${TMP_DIR}"
	log "Installed verified Dagu ${DAGU_VERSION} rebuilt with Go 1.27.3."
fi

# Prefer the copy we manage; only fall back to a pre-existing one on PATH.
if [[ -x "${BIN_DIR}/dagu" ]]; then
	DAGU_BIN="${BIN_DIR}/dagu"
else
	DAGU_BIN="$(command -v dagu)"
fi

case ":${ORIG_PATH}:" in
	*":${BIN_DIR}:"*) ;;
	*) warn "${BIN_DIR} is not on your PATH — add it to your shell profile so you can run dagu directly: export PATH=\"${BIN_DIR}:\$PATH\"" ;;
esac


# ---------------------------------------------------------------------------
log "Installing Node.js dependencies and building the SvelteKit app"
# ---------------------------------------------------------------------------
command -v node >/dev/null 2>&1 || die "Install a patched Node.js runtime (22.23.4, 24.22.1, or 26.12.1+) before running setup."

if ! node -e 'const [major, minor, patch] = process.versions.node.split(".").map(Number); const floors = {22:[23,4],24:[22,1],26:[12,1]}; const f=floors[major]; process.exit(f && (minor>f[0] || (minor===f[0] && patch>=f[1])) ? 0 : 1)'; then
	die "Node.js 22.23.4, 24.22.1, or 26.12.1+ on those release lines is required for the October 2026 certificate fix."
fi
NODE_BIN="$(command -v node)"

pushd "${SCRIPT_DIR}/web" >/dev/null
npm ci
npm run build
popd >/dev/null

log "Syncing web app to ${DISTILL_HOME}/web"
rsync -a --delete \
	--exclude 'node_modules' \
	--exclude '.svelte-kit' \
	"${SCRIPT_DIR}/web/build/" "${DISTILL_HOME}/web/build/"
rsync -a "${SCRIPT_DIR}/web/package.json" "${DISTILL_HOME}/web/package.json"
mkdir -p "${DISTILL_HOME}/web/node_modules"
rsync -a "${SCRIPT_DIR}/web/node_modules/" "${DISTILL_HOME}/web/node_modules/"

# ---------------------------------------------------------------------------
log "Installing Dagu config, DAGs, and scripts"
# ---------------------------------------------------------------------------
# Substitute basic-auth credentials into config.yaml (envsubst-style, no
# external dependency needed).
sed -e "s/\${DAGU_USER:-admin}/${DAGU_USER}/" \
	-e "s/\${DAGU_PASSWORD:-admin}/${DAGU_PASSWORD}/" \
	"${SCRIPT_DIR}/dagu/config.yaml" > "${DISTILL_HOME}/dagu/config.yaml"

rsync -a --delete "${SCRIPT_DIR}/dagu/dags/" "${DISTILL_HOME}/dagu/dags/"
rsync -a "${SCRIPT_DIR}/scripts/" "${DISTILL_HOME}/scripts/"
chmod +x "${DISTILL_HOME}"/scripts/*.py "${DISTILL_HOME}"/scripts/*.sh

# ---------------------------------------------------------------------------
log "Writing environment file"
# ---------------------------------------------------------------------------
cat > "${ENV_FILE}" <<EOF
DISTILL_HOME=${DISTILL_HOME}
DAGU_BASE_URL=http://127.0.0.1:${DAGU_PORT}
DAGU_PORT=${DAGU_PORT}
DAGU_USER=${DAGU_USER}
DAGU_PASSWORD=${DAGU_PASSWORD}
ADMIN_PASSWORD=${ADMIN_PASSWORD}
WEB_PASSWORD=${WEB_PASSWORD}
PORT=${WEB_PORT}
BODY_SIZE_LIMIT=Infinity
DIAGNOSTIC_AGENT=${DIAGNOSTIC_AGENT}
DIAGNOSTIC_MODEL=${DIAGNOSTIC_MODEL}
DIAGNOSTIC_OLLAMA_URL=${DIAGNOSTIC_OLLAMA_URL}
OPENAI_API_KEY=${OPENAI_API_KEY}
TOPIC_CHAT_MODEL=${TOPIC_CHAT_MODEL}
SSL_CERT_FILE=${CA_BUNDLE}
SSL_CERT_DIR=${DISTILL_HOME}/empty-ca-directory
HF_HUB_DISABLE_XET=1
EOF
chmod 600 "${ENV_FILE}"

# ---------------------------------------------------------------------------
log "Copying start/stop scripts and launchd plists"
# ---------------------------------------------------------------------------
cp "${SCRIPT_DIR}/start.sh" "${DISTILL_HOME}/start.sh"
cp "${SCRIPT_DIR}/stop.sh" "${DISTILL_HOME}/stop.sh"
chmod +x "${DISTILL_HOME}/start.sh" "${DISTILL_HOME}/stop.sh"

LAUNCHD_DIR="$HOME/Library/LaunchAgents"
mkdir -p "${LAUNCHD_DIR}"

DAGU_PLIST="${LAUNCHD_DIR}/com.distill.dagu.plist"
WEB_PLIST="${LAUNCHD_DIR}/com.distill.web.plist"

cat > "${DAGU_PLIST}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key><string>com.distill.dagu</string>
	<key>ProgramArguments</key>
	<array>
		<string>${DAGU_BIN}</string>
		<string>start-all</string>
		<string>--config</string>
		<string>${DISTILL_HOME}/dagu/config.yaml</string>
	</array>
	<key>RunAtLoad</key><true/>
	<key>KeepAlive</key><true/>
	<key>StandardOutPath</key><string>${DISTILL_HOME}/logs/dagu.out.log</string>
	<key>StandardErrorPath</key><string>${DISTILL_HOME}/logs/dagu.err.log</string>
	<key>EnvironmentVariables</key>
	<dict>
		<key>DAGU_USER</key><string>${DAGU_USER}</string>
		<key>DAGU_PASSWORD</key><string>${DAGU_PASSWORD}</string>
		<!-- launchd's default PATH is just /usr/bin:/bin:/usr/sbin:/sbin, which
		     misses Homebrew/uv/npm-global. Dagu's steps use absolute paths for
		     the venv and dagu itself, but the failure-handler diagnosis step
		     shells out to claude/codex by name, so PATH needs to include
		     wherever those CLIs are installed. -->
		<key>PATH</key><string>${EXTRA_PATH}:/usr/bin:/bin:/usr/sbin:/sbin</string>
		<key>SSL_CERT_FILE</key><string>${CA_BUNDLE}</string>
		<key>SSL_CERT_DIR</key><string>${DISTILL_HOME}/empty-ca-directory</string>
		<key>HF_HUB_DISABLE_XET</key><string>1</string>
		<key>DIAGNOSTIC_AGENT</key><string>${DIAGNOSTIC_AGENT}</string>
		<key>DIAGNOSTIC_MODEL</key><string>${DIAGNOSTIC_MODEL}</string>
		<key>DIAGNOSTIC_OLLAMA_URL</key><string>${DIAGNOSTIC_OLLAMA_URL}</string>
	</dict>
	<key>LimitLoadToSessionType</key><string>Background</string>
</dict>
</plist>
EOF

cat > "${WEB_PLIST}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key><string>com.distill.web</string>
	<key>ProgramArguments</key>
	<array>
		<string>${NODE_BIN}</string>
		<string>${DISTILL_HOME}/web/build/index.js</string>
	</array>
	<key>RunAtLoad</key><true/>
	<key>KeepAlive</key><true/>
	<key>StandardOutPath</key><string>${DISTILL_HOME}/logs/web.out.log</string>
	<key>StandardErrorPath</key><string>${DISTILL_HOME}/logs/web.err.log</string>
	<key>EnvironmentVariables</key>
	<dict>
		<key>DISTILL_HOME</key><string>${DISTILL_HOME}</string>
		<key>DAGU_BASE_URL</key><string>http://127.0.0.1:${DAGU_PORT}</string>
		<key>DAGU_USER</key><string>${DAGU_USER}</string>
		<key>DAGU_PASSWORD</key><string>${DAGU_PASSWORD}</string>
		<key>ADMIN_PASSWORD</key><string>${ADMIN_PASSWORD}</string>
		<key>WEB_PASSWORD</key><string>${WEB_PASSWORD}</string>
		<key>PORT</key><string>${WEB_PORT}</string>
		<key>BODY_SIZE_LIMIT</key><string>Infinity</string>
		<key>DIAGNOSTIC_MODEL</key><string>${DIAGNOSTIC_MODEL}</string>
		<key>DIAGNOSTIC_OLLAMA_URL</key><string>${DIAGNOSTIC_OLLAMA_URL}</string>
		<key>OPENAI_API_KEY</key><string>${OPENAI_API_KEY}</string>
		<key>TOPIC_CHAT_MODEL</key><string>${TOPIC_CHAT_MODEL}</string>
	</dict>
	<key>LimitLoadToSessionType</key><string>Background</string>
</dict>
</plist>
EOF
chmod 600 "${DAGU_PLIST}" "${WEB_PLIST}"

log "Loading launchd services"
LAUNCHD_DOMAIN="user/$(id -u)"
launchctl bootout "${LAUNCHD_DOMAIN}/com.distill.dagu" >/dev/null 2>&1 || true
launchctl bootout "${LAUNCHD_DOMAIN}/com.distill.web" >/dev/null 2>&1 || true

# bootout is asynchronous. Give the old processes a moment to release their
# ports, then fail clearly if another application owns either one.
for _ in {1..20}; do
	if ! nc -z 127.0.0.1 "${DAGU_PORT}" >/dev/null 2>&1 \
		&& ! nc -z 127.0.0.1 "${WEB_PORT}" >/dev/null 2>&1; then
		break
	fi
	sleep 0.1
done
nc -z 127.0.0.1 "${DAGU_PORT}" >/dev/null 2>&1 \
	&& die "Port ${DAGU_PORT} is already in use. Re-run with DAGU_PORT=<free port>."
nc -z 127.0.0.1 "${WEB_PORT}" >/dev/null 2>&1 \
	&& die "Port ${WEB_PORT} is already in use. Re-run with WEB_PORT=<free port>."

launchctl bootstrap "${LAUNCHD_DOMAIN}" "${DAGU_PLIST}"
launchctl bootstrap "${LAUNCHD_DOMAIN}" "${WEB_PLIST}"

wait_for_port() {
	local service="$1"
	local port="$2"
	for _ in {1..50}; do
		if nc -z 127.0.0.1 "${port}" >/dev/null 2>&1; then
			return 0
		fi
		sleep 0.1
	done
	die "${service} did not start on port ${port}. Check ${DISTILL_HOME}/logs for details."
}

wait_for_port "Dagu" "${DAGU_PORT}"
wait_for_port "Web app" "${WEB_PORT}"

# ---------------------------------------------------------------------------
LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || echo "<mac-studio-ip>")"

cat <<SUMMARY

────────────────────────────────────────────────────────────────
 Distillation Job Scheduler — setup complete
────────────────────────────────────────────────────────────────
 Student web UI:   http://${LAN_IP}:${WEB_PORT}   (also http://localhost:${WEB_PORT})
   Student password: ${WEB_PASSWORD}
 Admin panel:      http://${LAN_IP}:${WEB_PORT}/admin
   Admin password: ${ADMIN_PASSWORD}

 Dagu operator UI: http://${LAN_IP}:${DAGU_PORT}
   Username:       ${DAGU_USER}
   Password:       ${DAGU_PASSWORD}

 Credentials ${CREDS_NOTE}
 They are also saved to: ${DISTILL_HOME}/env

 Services run via launchd and start automatically on boot:
   com.distill.dagu   (Dagu server, port ${DAGU_PORT})
   com.distill.web    (SvelteKit app, port ${WEB_PORT})

 Manual controls:
   ${DISTILL_HOME}/start.sh   # start both services now
   ${DISTILL_HOME}/stop.sh    # stop both services
   launchctl list | grep com.distill

 Re-run this script anytime to update the stack in place.
────────────────────────────────────────────────────────────────
SUMMARY
