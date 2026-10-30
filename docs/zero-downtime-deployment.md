# Classroom web deployments

The Mac Studio checks the canonical repository's `main` branch every 60 seconds.
A push from any machine is picked up automatically. The watcher uses outbound
Git access; there is no public deployment webhook or GitHub runner on the Studio.
Only commits on `main` deploy. Local changes and divergent history stop deployment.

The public route is Cloudflare Tunnel → Moonshine's dedicated Caddy proxy on
localhost:3004 → an immutable web release on a separate localhost port. Its Caddy
admin API is localhost:2028; the unrelated shared Caddy instance uses port 2029.
Users access <https://moonshine.valleychristianai.com>.

## What a deployment does

`scripts/deploy_web.py` fetches canonical `main` and fast-forwards the clean
checkout at `/Users/aimac/Developer/mine/moonshine`. The `origin` remote on this
machine is a fork, so the deployer fetches the canonical URL explicitly.

It creates a release under `~/.distill/releases/<commit>-<attempt>`, installs the locked
dependencies, runs Svelte checks and backend tests, and builds the web bundle.
It publishes both versions' hashed browser assets to an append-only directory
served directly by the proxy before starting the new web process. Existing tabs
can load their original code after a switch or rollback; existing asset files
are never rewritten while users are reading them.

Before switching, it verifies login, an authenticated data request, availability
of every configured student model, an existing job and its JSONL preview when
available, and the previous browser entry assets. It then gracefully loads the
new proxy upstream while monitoring the public site and repeating authenticated
checks through the public hostname. Existing requests can finish on the old
process. The tunnel and job runner are not restarted.

Build failures leave traffic on the current release. Failed public checks restore
the previous proxy configuration, leaving any candidate requests to finish.
Recovery information is written before the
switch so the next run can roll back an interrupted deployment. The active and
previous web processes are retained; older managed processes are stopped only
after they have no established TCP connections. Release files and their assets
are retained.

The automation deploys the **web application**. Changes to the queue runner,
training environment, model files, or operating-system services need a separate
maintenance procedure; it does not restart those services or install their changes.

## Operate it

Run on `valley-big-mac`:

```sh
cd /Users/aimac/Developer/mine/moonshine
python3 scripts/deploy_web.py install  # install/update the minute watcher
python3 scripts/deploy_web.py deploy   # deploy canonical main immediately
python3 scripts/deploy_web.py status   # active release and last result
```

`./setup.sh` delegates to this deployer when an existing zero-downtime proxy
configuration is present. Its original bootstrap behavior remains for new installs.

The watcher is `com.distill.autodeploy` in launchd's `user/501` domain. It uses a
nonblocking deployment lock, so manual and automatic runs cannot overlap. Its
installed script updates only after a successful deployment. It stays quiet when
`main` has not changed. A failed commit is not retried every minute; push a fix to
`main` or run `deploy` manually to retry it. Build logs and failure records are
private files under `~/.distill/deploy`; credentials must not be printed from
environment files or launchd plists.

To pause or resume automatic deployment without affecting the website:

```sh
launchctl bootout user/$(id -u)/com.distill.autodeploy
launchctl bootstrap user/$(id -u) ~/Library/LaunchAgents/com.distill.autodeploy.plist
```

Do not unload the watcher while it is deploying; let that attempt finish first.
Stopping a build before cutover does not affect live traffic, but stopping during
cutover leaves a pending transaction for the next run to recover.

## Roll back

The startup proxy config is `~/.distill/deploy/proxy.json`; release metadata is
`active-release.json`. The previous versions are `proxy.previous.json` and
`previous-release.json`. Pause automatic deployment, then load the previous config
through the dedicated admin API and restore **both** the startup config and
active release metadata. The previous app remains running, so rollback needs no
web process or tunnel restart. Investigate the failed commit before resuming.

The original app on port 3001 remains available as an additional fallback. Its
old direct Cloudflare connector is disabled: do not enable it while the public
site is using the proxy, because that would mix releases.
