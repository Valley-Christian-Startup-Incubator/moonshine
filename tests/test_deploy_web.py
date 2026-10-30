"""Failure isolation for the classroom's zero-downtime deployment switch."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('deploy_web', Path(__file__).parents[1] / 'scripts/deploy_web.py')
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)

class PassingProbes:
    def __init__(self, host):
        self.results = [200, 200]
    def __enter__(self):
        return self
    def __exit__(self, *_):
        pass
    def verify(self):
        pass

class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.config = {'apps': {'http': {'servers': {'moonshine': {'routes': [
            {'handle': [{'upstreams': [{'dial': '127.0.0.1:3002'}]}]}]}}}}}
        self.active = {'commit': 'old', 'port': 3002, 'publicHost': 'moonshine.test'}
        self.candidate = {'commit': 'new', 'port': 3200, 'publicHost': 'moonshine.test'}
        deploy.save_json(self.directory / 'proxy.json', self.config)
        deploy.save_json(self.directory / 'active-release.json', self.active)
        self.probes = patch.object(deploy, 'PublicProbes', PassingProbes)
        self.sleep = patch.object(deploy.time, 'sleep')
        self.probes.start()
        self.sleep.start()
    def tearDown(self):
        self.probes.stop()
        self.sleep.stop()
        self.temporary.cleanup()
    def test_success_keeps_rollback_config_and_release(self):
        with patch.object(deploy, 'proxy_load') as load:
            deploy.switch_release(self.directory, self.candidate, lambda: None)
        self.assertEqual(deploy.proxy_upstream(load.call_args.args[0]), [{'dial': '127.0.0.1:3200'}])
        self.assertEqual(deploy.read_json(self.directory / 'active-release.json')['commit'], 'new')
        self.assertEqual(deploy.read_json(self.directory / 'previous-release.json'), self.active)
        self.assertEqual(deploy.read_json(self.directory / 'proxy.previous.json'), self.config)
        self.assertFalse((self.directory / 'pending-switch.json').exists())
    def test_failed_authenticated_check_restores_runtime_and_startup_config(self):
        def unhealthy():
            raise RuntimeError('candidate health failed')
        with patch.object(deploy, 'proxy_load') as load:
            with self.assertRaisesRegex(RuntimeError, 'candidate health'):
                deploy.switch_release(self.directory, self.candidate, unhealthy)
        self.assertEqual(load.call_count, 2)
        self.assertEqual(load.call_args.args[0], self.config)
        self.assertEqual(deploy.read_json(self.directory / 'proxy.json'), self.config)
        self.assertEqual(deploy.read_json(self.directory / 'active-release.json'), self.active)
        self.assertFalse((self.directory / 'pending-switch.json').exists())
    def test_proxy_rejecting_new_config_restores_previous_release(self):
        with patch.object(deploy, 'proxy_load', side_effect=[RuntimeError('rejected'), None]) as load:
            with self.assertRaisesRegex(RuntimeError, 'rejected'):
                deploy.switch_release(self.directory, self.candidate, lambda: None)
        self.assertEqual(load.call_args.args[0], self.config)
        self.assertEqual(deploy.read_json(self.directory / 'active-release.json'), self.active)
    def test_failed_rollback_keeps_recovery_transaction(self):
        with patch.object(deploy, 'proxy_load', side_effect=RuntimeError('unavailable')):
            with self.assertRaises(RuntimeError):
                deploy.switch_release(self.directory, self.candidate, lambda: None)
        self.assertTrue((self.directory / 'pending-switch.json').exists())
        with patch.object(deploy, 'proxy_load') as load:
            deploy.recover_switch(self.directory)
        self.assertEqual(load.call_args.args[0], self.config)
        self.assertEqual(deploy.read_json(self.directory / 'active-release.json'), self.active)
        self.assertFalse((self.directory / 'pending-switch.json').exists())
    def test_interrupted_switch_recovers_previous_version(self):
        deploy.save_json(self.directory / 'pending-switch.json', {'config': self.config, 'release': self.active})
        deploy.save_json(self.directory / 'proxy.json', {'incomplete': True})
        deploy.save_json(self.directory / 'active-release.json', self.candidate)
        with patch.object(deploy, 'proxy_load'):
            deploy.recover_switch(self.directory)
        self.assertEqual(deploy.read_json(self.directory / 'proxy.json'), self.config)
        self.assertEqual(deploy.read_json(self.directory / 'active-release.json'), self.active)
    def test_busy_deployer_does_not_fetch_or_touch_services(self):
        home = self.directory / '.distill'
        (home / 'deploy').mkdir(parents=True)
        with (home / 'deploy/deploy.lock').open('a') as lock:
            deploy.fcntl.flock(lock, deploy.fcntl.LOCK_EX | deploy.fcntl.LOCK_NB)
            with patch.object(deploy, 'git') as git, patch.object(deploy, 'proxy_load') as load:
                self.assertEqual(deploy.deploy(home, self.directory), {'status': 'busy'})
            git.assert_not_called()
            load.assert_not_called()
    def test_failed_build_removes_candidate_files(self):
        home = self.directory / '.distill'
        (home / 'releases').mkdir(parents=True)
        source = {'ProgramArguments': ['/usr/bin/node']}
        with tempfile.TemporaryFile() as log, patch.object(deploy, 'run', side_effect=RuntimeError('build failed')):
            with self.assertRaisesRegex(RuntimeError, 'build failed'):
                deploy.build_release(home, self.directory, 'failed-commit', source, log)
        self.assertEqual(list((home / 'releases').iterdir()), [])
    def test_shared_assets_survive_a_rollback_and_are_never_rewritten(self):
        home = self.directory / '.distill'
        (home / 'deploy').mkdir(parents=True)
        deploy.save_json(home / 'deploy/proxy.json', self.config)
        old = self.directory / 'old-assets'
        new = self.directory / 'new-assets'
        old.mkdir(); new.mkdir()
        (old / 'old-hash.js').write_text('old tab code')
        (new / 'new-hash.js').write_text('new tab code')
        with patch.object(deploy, 'proxy_load') as load:
            deploy.publish_assets(home, old, new)
            config = deploy.read_json(home / 'deploy/proxy.json')
            self.assertEqual(deploy.proxy_upstream(config), deploy.proxy_upstream(self.config))
            root = home / 'deploy/client/_app/immutable'
            self.assertEqual((root / 'old-hash.js').read_text(), 'old tab code')
            self.assertEqual((root / 'new-hash.js').read_text(), 'new tab code')
            timestamp = (root / 'old-hash.js').stat().st_mtime_ns
            deploy.publish_assets(home, old, new)
            self.assertEqual((root / 'old-hash.js').stat().st_mtime_ns, timestamp)
            self.assertEqual(load.call_count, 1)
    def test_install_cannot_interrupt_running_deployment(self):
        home = self.directory / '.distill'
        (home / 'deploy').mkdir(parents=True)
        with (home / 'deploy/deploy.lock').open('a') as lock:
            deploy.fcntl.flock(lock, deploy.fcntl.LOCK_EX | deploy.fcntl.LOCK_NB)
            with patch.object(deploy, 'install_watcher') as install:
                with self.assertRaisesRegex(RuntimeError, 'deployment is running'):
                    deploy.install(home, self.directory)
            install.assert_not_called()

if __name__ == '__main__':
    unittest.main()
