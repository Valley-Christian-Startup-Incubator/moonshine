# Deployment

Read [the deployment procedure](docs/zero-downtime-deployment.md) before deploying
Moonshine, changing its automation, or rolling back.

Canonical `main` auto-deploys to the classroom Mac Studio through
`scripts/deploy_web.py`. Use that script for immediate manual deployments too.
The Studio's `origin` remote is a fork; the deployer fetches the canonical
Valley-Christian-Startup-Incubator repository explicitly.

Web deployments must preserve service availability and existing requests. Build
an isolated release, verify it, and switch Moonshine's dedicated proxy gracefully.
Do not restart the public tunnel, current web process, or queue runner as a web
deployment step. Keep the previous process and browser assets for rollback.
Queue scripts, model files, and system dependencies need a separate maintenance
procedure; the web auto-deployer does not install those changes.

Keep credentials in private files on the Studio. Do not print environment-file
contents, launchd environments, or credential-bearing command arguments.
