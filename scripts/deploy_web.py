#!/usr/bin/env python3
"""Build and switch Moonshine's web UI without restarting live services.

Run `install` once on the classroom Mac to watch canonical main every minute.
Run `deploy` for an immediate attempt, or `status` to inspect the last result.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request


CANONICAL = 'https://github.com/Valley-Christian-Startup-Incubator/moonshine.git'
REF = 'refs/remotes/moonshine-deploy/main'
WATCHER = 'com.distill.autodeploy'
DEFAULT_CHECKOUT = Path('/Users/aimac/Developer/mine/moonshine')
DEFAULT_HOME = Path.home() / '.distill'


def atomic_write(file, data, mode=0o600):
    file = Path(file)
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_name(file.name + '.tmp')
    temporary.write_bytes(data if isinstance(data, bytes) else data.encode())
    temporary.chmod(mode)
    temporary.replace(file)


def save_json(file, data):
    atomic_write(file, json.dumps(data, indent=2) + '\n')


def read_json(file, default=None):
    try:
        return json.loads(Path(file).read_text())
    except FileNotFoundError:
        return default


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def git(checkout, *args):
    return subprocess.check_output(['git', '-C', str(checkout), *args], text=True).strip()


def curl(url, cookie=None):
    # Cookies go through stdin, never shell arguments, logs, or status files.
    config = 'header = "Cookie: ' + cookie + '"\n' if cookie else ''
    result = run(['curl', '--silent', '--show-error', '--max-time', '15',
                  '--config', '-', '--write-out', '\n%{http_code}', url],
                 input=config, text=True, capture_output=True)
    body, status = result.stdout.rsplit('\n', 1)
    if status != '200':
        raise RuntimeError('HTTP ' + status + ' from deployment health check')
    return body


class PublicProbes:
    def __init__(self, host):
        self.host = host
        self.results = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._watch, daemon=True)

    def _watch(self):
        while not self.stop.is_set():
            try:
                curl('https://' + self.host + '/login?deployment_probe=' + secrets.token_hex(8))
                self.results.append(200)
            except Exception:
                self.results.append('failed')
            self.stop.wait(0.25)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join()

    def verify(self):
        if not self.results or any(result != 200 for result in self.results):
            raise RuntimeError('Public monitoring failed; keeping the previous release')


def proxy_load(config):
    # This is Moonshine's dedicated admin API, not the shared Caddy instance.
    request = urllib.request.Request('http://127.0.0.1:2028/load',
                                     data=json.dumps(config).encode(),
                                     headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status != 200:
            raise RuntimeError('Proxy did not accept the new configuration')


def proxy_upstream(config):
    return config['apps']['http']['servers']['moonshine']['routes'][0]['handle'][0]['upstreams']


def switch_release(directory, candidate, verify, hold_seconds=10):
    """Persist recovery information before loading config; roll back on any failure."""
    previous_config = read_json(directory / 'proxy.json')
    previous_release = read_json(directory / 'active-release.json')
    transaction = {'config': previous_config, 'release': previous_release}
    save_json(directory / 'pending-switch.json', transaction)
    next_config = json.loads(json.dumps(previous_config))
    next_config['apps']['http']['servers']['moonshine']['routes'][0]['handle'][0]['upstreams'] = [
        {'dial': '127.0.0.1:' + str(candidate['port'])}]
    try:
        with PublicProbes(candidate['publicHost']) as probes:
            time.sleep(1)
            probes.verify()
            # A proxy restart during the switch also starts the verified candidate.
            save_json(directory / 'proxy.json', next_config)
            proxy_load(next_config)
            verify()
            time.sleep(hold_seconds)
        probes.verify()
        candidate['publicChecks'] = len(probes.results)
        save_json(directory / 'proxy.previous.json', previous_config)
        save_json(directory / 'previous-release.json', previous_release)
        save_json(directory / 'active-release.json', candidate)
    except BaseException:
        # Keep the transaction until both runtime and startup config are restored.
        save_json(directory / 'proxy.json', previous_config)
        proxy_load(previous_config)
        save_json(directory / 'active-release.json', previous_release)
        (directory / 'pending-switch.json').unlink()
        raise
    (directory / 'pending-switch.json').unlink()


def recover_switch(directory):
    pending = read_json(directory / 'pending-switch.json')
    if pending:
        save_json(directory / 'proxy.json', pending['config'])
        proxy_load(pending['config'])
        save_json(directory / 'active-release.json', pending['release'])
        (directory / 'pending-switch.json').unlink()


def service_for(home, port):
    for file in (home.parent / 'Library/LaunchAgents').glob('com.distill.web*.plist'):
        service = plistlib.loads(file.read_bytes())
        if str(service.get('EnvironmentVariables', {}).get('PORT')) == str(port):
            return service
    raise RuntimeError('Cannot find the active web service environment')


@contextlib.contextmanager
def verification_session(home):
    users = read_json(home / 'users.json', [])
    if not users:
        raise RuntimeError('An existing user name is needed for authenticated deployment checks')
    token = secrets.token_urlsafe(32)
    session = home / 'auth/sessions' / (hashlib.sha256(token.encode()).hexdigest() + '.json')
    save_json(session, {'createdAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                        'expiresAt': int(time.time() * 1000) + 600_000})
    try:
        yield 'distill_session=' + token + '; moonshine_user=' + urllib.parse.quote(users[0]['name'])
    finally:
        session.unlink(missing_ok=True)


def verify_app(base, home, cookie, old_assets=None):
    curl(base + '/login')
    # The data endpoint checks the app's actual configured models and auth, with
    # no dependency on Rollup's changing export names or chunk filenames.
    data = json.loads(curl(base + '/__data.json', cookie))
    available = None
    for node in data.get('nodes', []):
        if not node or node.get('type') != 'data':
            continue
        flat = node.get('data', [])
        if flat and isinstance(flat[0], dict) and 'students' in flat[0]:
            students = flat[flat[0]['students']]
            available = [{key: flat[row[key]] for key in ('id', 'label', 'available')}
                         for row in (flat[index] for index in students)]
    if not available or any(model['available'] is not True for model in available):
        raise RuntimeError('Configured student models are missing or unavailable')
    for meta in sorted((home / 'jobs').glob('*/meta.json')):
        job_id = meta.parent.name
        if (home / 'results' / job_id / 'output.jsonl').exists():
            curl(base + '/jobs/' + job_id, cookie)
            preview = json.loads(curl(base + '/jobs/' + job_id + '/preview?file=questions', cookie))
            if not isinstance(preview.get('rows'), list):
                raise RuntimeError('JSONL preview health check failed')
            break
    if old_assets:
        for asset in old_assets:
            curl(base + '/_app/immutable/' + asset)
    return available


def available_port():
    for port in range(3200, 3300):
        with socket.socket() as sock:
            try:
                sock.bind(('127.0.0.1', port))
                return port
            except OSError:
                pass
    raise RuntimeError('No deployment port is available')


def active_build(home, active):
    service = service_for(home, active['port'])
    return Path(service['ProgramArguments'][1]).parent


def retire_idle_releases(home):
    """Keep current + rollback instances; retire only older, disconnected releases."""
    active = read_json(home / 'deploy/active-release.json', {})
    previous = read_json(home / 'deploy/previous-release.json', {})
    keep_ports = {str(item.get('port')) for item in (active, previous)}
    for file in (home.parent / 'Library/LaunchAgents').glob('com.distill.web.release.*.plist'):
        service = plistlib.loads(file.read_bytes())
        port = str(service.get('EnvironmentVariables', {}).get('PORT'))
        if port in keep_ports:
            continue
        connections = subprocess.run(['lsof', '-nP', '-iTCP:' + port, '-sTCP:ESTABLISHED'], capture_output=True)
        if connections.returncode == 1:  # lsof: no matching sockets
            stop_candidate(home, service['Label'])


def start_candidate(home, release, commit, source_service):
    port = available_port()
    label = 'com.distill.web.release.' + commit[:12]
    service = dict(source_service)
    service['EnvironmentVariables'] = dict(source_service['EnvironmentVariables'])
    service.update(Label=label, WorkingDirectory=str(release / 'web'),
                   ProgramArguments=[source_service['ProgramArguments'][0], str(release / 'web/build/index.js')],
                   StandardOutPath=str(home / 'logs' / (label + '.out.log')),
                   StandardErrorPath=str(home / 'logs' / (label + '.err.log')))
    service['EnvironmentVariables'].update(PORT=str(port), HOST='127.0.0.1')
    file = home.parent / 'Library/LaunchAgents' / (label + '.plist')
    if file.exists():
        raise RuntimeError('This release service already exists; inspect it before retrying')
    atomic_write(file, plistlib.dumps(service))
    try:
        run(['launchctl', 'bootstrap', 'user/' + str(os.getuid()), str(file)], capture_output=True)
        for _ in range(50):
            try:
                curl('http://127.0.0.1:' + str(port) + '/login')
                return {'port': port, 'service': label, 'release': str(release)}
            except Exception:
                time.sleep(0.2)
        raise RuntimeError('Candidate did not become healthy')
    except BaseException:
        stop_candidate(home, label)
        raise


def stop_candidate(home, label):
    if not label.startswith('com.distill.web.release.'):
        raise RuntimeError('Only managed candidate services may be stopped')
    subprocess.run(['launchctl', 'bootout', 'user/' + str(os.getuid()) + '/' + label], capture_output=True)
    (home.parent / 'Library/LaunchAgents' / (label + '.plist')).unlink(missing_ok=True)


def build_release(home, checkout, commit, source_service, log):
    release = home / 'releases' / commit
    if release.exists():
        raise RuntimeError('Release directory already exists; inspect the previous attempt before retrying')
    release.mkdir(parents=True)
    try:
        with tempfile.TemporaryFile() as archive:
            run(['git', '-C', str(checkout), 'archive', commit], stdout=archive, stderr=log)
            archive.seek(0)
            run(['tar', '-x', '-C', str(release)], stdin=archive, stderr=log)
        web = release / 'web'
        if (release / 'tests/test_deploy_web.py').exists():
            run(['/usr/bin/python3', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_deploy_web.py'],
                cwd=release, stdout=log, stderr=log)
        node = source_service['ProgramArguments'][0]
        env = dict(os.environ)
        env['PATH'] = str(Path(node).parent) + ':/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'
        for command in (['npm', 'ci'], ['npm', 'run', 'check']):
            run(command, cwd=web, env=env, stdout=log, stderr=log)
        tests = sorted(str(p.relative_to(web)) for p in (web / 'tests').glob('*.test.mjs'))
        if tests:
            run([node, '--experimental-strip-types', '--test', *tests], cwd=web, env=env, stdout=log, stderr=log)
        run(['npm', 'run', 'build'], cwd=web, env=env, stdout=log, stderr=log)
        return release
    except BaseException:
        shutil.rmtree(release)
        raise


def deploy(home, checkout, automatic=False):
    directory = home / 'deploy'
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'deploy.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'busy'}
        recover_switch(directory)
        if git(checkout, 'branch', '--show-current') != 'main' or git(checkout, 'status', '--porcelain'):
            raise RuntimeError('Deployment checkout must be clean and on main')
        run(['git', '-C', str(checkout), 'fetch', '--quiet', CANONICAL, 'main:' + REF], capture_output=True)
        commit = git(checkout, 'rev-parse', REF)
        active = read_json(directory / 'active-release.json')
        if not active:
            raise RuntimeError('Existing zero-downtime deployment metadata is required')
        config = read_json(directory / 'proxy.json')
        live_config = json.load(urllib.request.urlopen('http://127.0.0.1:2028/config/', timeout=10))
        expected = [{'dial': '127.0.0.1:' + str(active['port'])}]
        if proxy_upstream(config) != expected or proxy_upstream(live_config) != expected:
            raise RuntimeError('Proxy and active release metadata disagree; inspect before deploying')
        if git(checkout, 'rev-parse', active['commit']) == commit:
            retire_idle_releases(home)
            return {'status': 'unchanged', 'commit': commit}
        failed = read_json(directory / 'last-failure.json', {})
        if automatic and failed.get('commit') == commit:
            return {'status': 'failed-commit-skipped', 'commit': commit}
        run(['git', '-C', str(checkout), 'merge-base', '--is-ancestor', 'HEAD', commit], capture_output=True)
        run(['git', '-C', str(checkout), 'merge', '--ff-only', '--quiet', commit], capture_output=True)
        log_file = directory / ('build-' + commit[:12] + '.log')
        candidate = None
        release = None
        switched = False
        try:
            with log_file.open('w') as log:
                log_file.chmod(0o600)
                source_service = service_for(home, active['port'])
                old_build = active_build(home, active)
                release = build_release(home, checkout, commit, source_service, log)
            # Keep hashed code for existing browser tabs; the new server indexes
            # this union of assets at startup, before it receives public traffic.
            old_assets = old_build / 'client/_app/immutable'
            shutil.copytree(old_assets, release / 'web/build/client/_app/immutable', dirs_exist_ok=True)
            asset_samples = [str(p.relative_to(old_assets)) for p in sorted((old_assets / 'entry').glob('*.js'))]
            candidate = start_candidate(home, release, commit, source_service)
            candidate.update(commit=commit, previousPort=active['port'], proxyPort=3004,
                             publicHost=active['publicHost'], deployedAt=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
            with verification_session(home) as cookie:
                models = verify_app('http://127.0.0.1:' + str(candidate['port']), home, cookie, asset_samples)
                switch_release(directory, candidate,
                               lambda: verify_app('https://' + candidate['publicHost'], home, cookie, asset_samples))
            switched = True
            # Only update the watcher after its own commit passes all checks.
            atomic_write(directory / 'deploy_web.py', (release / 'scripts/deploy_web.py').read_bytes(), 0o700)
            save_json(directory / 'last-success.json', {**candidate, 'models': models})
            (directory / 'last-failure.json').unlink(missing_ok=True)
            retire_idle_releases(home)
            return {'status': 'deployed', 'commit': commit, 'publicChecks': candidate['publicChecks'], 'models': models}
        except BaseException as error:
            if candidate and not switched and not (directory / 'pending-switch.json').exists():
                stop_candidate(home, candidate['service'])
                shutil.rmtree(candidate['release'])
            elif release and not switched and not (directory / 'pending-switch.json').exists():
                shutil.rmtree(release)
            # Record the exception type only: subprocess errors may contain
            # secrets in arguments or environment-derived diagnostic output.
            save_json(directory / 'last-failure.json', {'commit': commit, 'error': type(error).__name__,
                                                       'log': str(log_file), 'time': time.time()})
            raise


def install_watcher(home, checkout):
    if not (home / 'deploy/proxy.json').exists():
        raise RuntimeError('Initialize the zero-downtime proxy before installing the watcher')
    script = home / 'deploy/deploy_web.py'
    atomic_write(script, Path(__file__).read_bytes(), 0o700)
    service = {'Label': WATCHER, 'ProgramArguments': ['/usr/bin/python3', str(script), '--home', str(home),
                                                     '--checkout', str(checkout), 'watch'],
               'RunAtLoad': True, 'StartInterval': 60, 'Nice': 10, 'ProcessType': 'Background',
               'LimitLoadToSessionType': 'Background', 'StandardOutPath': str(home / 'logs/autodeploy.out.log'),
               'StandardErrorPath': str(home / 'logs/autodeploy.err.log')}
    file = home.parent / 'Library/LaunchAgents' / (WATCHER + '.plist')
    domain = 'user/' + str(os.getuid())
    subprocess.run(['launchctl', 'bootout', domain + '/' + WATCHER], capture_output=True)
    atomic_write(file, plistlib.dumps(service))
    run(['launchctl', 'bootstrap', domain, str(file)], capture_output=True)
    return {'status': 'installed', 'intervalSeconds': 60, 'branch': 'main', 'source': CANONICAL}


def install(home, checkout):
    directory = home / 'deploy'
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'deploy.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('A deployment is running; install the watcher after it finishes')
        return install_watcher(home, checkout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=DEFAULT_HOME)
    parser.add_argument('--checkout', type=Path, default=DEFAULT_CHECKOUT)
    parser.add_argument('command', choices=['deploy', 'watch', 'install', 'status'])
    args = parser.parse_args()
    try:
        if args.command == 'install':
            result = install(args.home, args.checkout)
        elif args.command == 'status':
            result = {name: read_json(args.home / 'deploy' / (name + '.json'))
                      for name in ('active-release', 'last-success', 'last-failure')}
        else:
            result = deploy(args.home, args.checkout, args.command == 'watch')
        if args.command != 'watch' or result['status'] == 'deployed':
            print(json.dumps(result))
    except Exception as error:
        # Keep credentials out of launchd output even on subprocess failures.
        print(json.dumps({'status': 'failed', 'error': type(error).__name__,
                          'message': str(error) if isinstance(error, RuntimeError) else 'Inspect the private deployment logs'}), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
