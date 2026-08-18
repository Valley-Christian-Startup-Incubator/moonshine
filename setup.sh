#!/usr/bin/env bash
# Bootstrap the distillation job scheduler on a Mac Studio (Apple Silicon).
# Installs Dagu, mlx-lm, builds the SvelteKit app, and wires up background
# services. Safe to re-run — every step is idempotent.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DISTILL_HOME="${DISTILL_HOME:-$HOME/.distill}"
VENV_DIR="${VENV_DIR:-$HOME/.distill-venv}"
DAGU_VERSION="${DAGU_VERSION:-1.16.6}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
WEB_PORT="${WEB_PORT:-3000}"
DAGU_PORT="${DAGU_PORT:-8081}"

# Common install locations for tools that aren't always on the default
# launchd PATH (Homebrew, uv, npm-global, the diagnostic agent CLIs).
EXTRA_PATH="/opt/homebrew/bin:/usr/local/bin:${HOME}/.local/bin:${HOME}/.cargo/bin:${HOME}/.npm-global/bin"
export PATH="${EXTRA_PATH}:${PATH}"

DAGU_USER="${DAGU_USER:-admin}"
DAGU_PASSWORD="${DAGU_PASSWORD:-$(openssl rand -hex 8)}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-$(openssl rand -hex 8)}"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$1" >&2; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$1" >&2; exit 1; }

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
	die "This script targets Apple Silicon Macs (Darwin/arm64)."
fi

command -v brew >/dev/null 2>&1 || die "Homebrew is required. Install it from https://brew.sh first."

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
log "Installing Dagu ${DAGU_VERSION}"
# ---------------------------------------------------------------------------
if command -v dagu >/dev/null 2>&1 && dagu version 2>/dev/null | grep -q "${DAGU_VERSION}"; then
	log "Dagu ${DAGU_VERSION} already installed, skipping."
else
	DAGU_TARBALL="dagu_${DAGU_VERSION}_darwin_arm64.tar.gz"
	DAGU_URL="https://github.com/dagu-org/dagu/releases/download/v${DAGU_VERSION}/${DAGU_TARBALL}"
	TMP_DIR="$(mktemp -d)"
	log "Downloading ${DAGU_URL}"
	curl -fsSL "${DAGU_URL}" -o "${TMP_DIR}/${DAGU_TARBALL}" \
		|| die "Failed to download Dagu. Check DAGU_VERSION or your network connection."
	tar -xzf "${TMP_DIR}/${DAGU_TARBALL}" -C "${TMP_DIR}"
	install -m 755 "${TMP_DIR}/dagu" /usr/local/bin/dagu
	rm -rf "${TMP_DIR}"
	log "Installed dagu $(dagu version 2>/dev/null || echo "${DAGU_VERSION}") to /usr/local/bin/dagu"
fi

# ---------------------------------------------------------------------------
log "Setting up Python ${PYTHON_VERSION} venv at ${VENV_DIR}"
# ---------------------------------------------------------------------------
# Prefer uv for this: it can fetch and manage its own Python builds, so we
# don't depend on whatever python3 (if any) happens to be on the system —
# and don't need Homebrew's python formula as a prerequisite either.
if ! command -v uv >/dev/null 2>&1; then
	log "uv not found, installing it (https://astral.sh/uv)"
	curl -LsSf https://astral.sh/uv/install.sh | sh \
		|| warn "Automatic uv install failed; falling back to a system python3 if one is present."
	export PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:${PATH}"
fi

if command -v uv >/dev/null 2>&1; then
	log "Using uv to provision Python ${PYTHON_VERSION} and the venv"
	uv python install "${PYTHON_VERSION}"
	if [[ ! -d "${VENV_DIR}" ]]; then
		uv venv "${VENV_DIR}" --python "${PYTHON_VERSION}"
	fi
	uv pip install --python "${VENV_DIR}/bin/python" --upgrade pip mlx-lm
	uv pip install --python "${VENV_DIR}/bin/python" mlx-tune \
		|| warn "mlx-tune not available on PyPI for this environment; skipping (mlx-lm's own LoRA trainer is used by finetune.yaml regardless)."
else
	log "uv unavailable, falling back to system python3"
	command -v python3 >/dev/null 2>&1 || die "Neither uv nor python3 is available. Install uv manually from https://astral.sh/uv and re-run."

	py_version="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
	py_major="${py_version%%.*}"
	py_minor="${py_version##*.}"
	if [[ "${py_major}" -lt 3 || ("${py_major}" -eq 3 && "${py_minor}" -lt 11) ]]; then
		die "python3 3.11+ required (found ${py_version}) and uv could not be installed automatically. Install uv from https://astral.sh/uv and re-run."
	fi

	if [[ ! -d "${VENV_DIR}" ]]; then
		python3 -m venv "${VENV_DIR}"
	fi
	"${VENV_DIR}/bin/pip" install --upgrade pip
	"${VENV_DIR}/bin/pip" install mlx-lm
	"${VENV_DIR}/bin/pip" install mlx-tune \
		|| warn "mlx-tune not available on PyPI for this environment; skipping (mlx-lm's own LoRA trainer is used by finetune.yaml regardless)."
fi

# ---------------------------------------------------------------------------
log "Installing Node.js dependencies and building the SvelteKit app"
# ---------------------------------------------------------------------------
if ! command -v node >/dev/null 2>&1; then
	log "Node.js not found, installing via Homebrew"
	brew install node
fi

node_major="$(node -v | sed 's/v//' | cut -d. -f1)"
if [[ "${node_major}" -lt 20 ]]; then
	die "Node.js 20+ required, found $(node -v)."
fi

pushd "${SCRIPT_DIR}/web" >/dev/null
npm install
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
ENV_FILE="${DISTILL_HOME}/env"
cat > "${ENV_FILE}" <<EOF
DISTILL_HOME=${DISTILL_HOME}
DAGU_BASE_URL=http://127.0.0.1:${DAGU_PORT}
DAGU_USER=${DAGU_USER}
DAGU_PASSWORD=${DAGU_PASSWORD}
ADMIN_PASSWORD=${ADMIN_PASSWORD}
PORT=${WEB_PORT}
BODY_SIZE_LIMIT=Infinity
DIAGNOSTIC_AGENT=${DIAGNOSTIC_AGENT:-}
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
		<string>/usr/local/bin/dagu</string>
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
		     shells out to `claude`/`codex` by name, so PATH needs to include
		     wherever those CLIs are installed. -->
		<key>PATH</key><string>${EXTRA_PATH}:/usr/bin:/bin:/usr/sbin:/sbin</string>
		<key>DIAGNOSTIC_AGENT</key><string>${DIAGNOSTIC_AGENT:-}</string>
	</dict>
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
		<string>/usr/bin/env</string>
		<string>node</string>
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
		<key>PORT</key><string>${WEB_PORT}</string>
		<key>BODY_SIZE_LIMIT</key><string>Infinity</string>
	</dict>
</dict>
</plist>
EOF

log "Loading launchd services"
launchctl unload "${DAGU_PLIST}" >/dev/null 2>&1 || true
launchctl unload "${WEB_PLIST}" >/dev/null 2>&1 || true
launchctl load "${DAGU_PLIST}"
launchctl load "${WEB_PLIST}"

# ---------------------------------------------------------------------------
LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || echo "<mac-studio-ip>")"

cat <<SUMMARY

────────────────────────────────────────────────────────────────
 Distillation Job Scheduler — setup complete
────────────────────────────────────────────────────────────────
 Student web UI:   http://${LAN_IP}:${WEB_PORT}   (also http://localhost:${WEB_PORT})
 Admin panel:      http://${LAN_IP}:${WEB_PORT}/admin
   Admin password: ${ADMIN_PASSWORD}

 Dagu operator UI: http://${LAN_IP}:${DAGU_PORT}
   Username:       ${DAGU_USER}
   Password:       ${DAGU_PASSWORD}

 Credentials are also saved to: ${DISTILL_HOME}/env

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
