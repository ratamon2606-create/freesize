import importlib.util
import pathlib
import unittest
from unittest.mock import patch
from types import SimpleNamespace

MOD = pathlib.Path(__file__).resolve().parents[1] / 'scripts' / 'freesize_local_deploy.py'
spec = importlib.util.spec_from_file_location('agent', MOD)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

class TestSafety(unittest.TestCase):
    def test_separate_docker_disk_allows_8_gib_root_and_43_gib_docker(self):
        def fake_stat(path):
            return SimpleNamespace(st_dev=1 if str(path) == '/' else 2)
        def fake_usage(path):
            return SimpleNamespace(free=(8 if str(path) == '/' else 43) * 1024**3)
        with patch.object(agent, 'run', return_value='/mnt/rosdata/docker'), \
             patch.object(agent.os, 'stat', side_effect=fake_stat), \
             patch.object(agent.shutil, 'disk_usage', side_effect=fake_usage):
            agent.disk_check()

    def test_separate_docker_disk_low_root_stops(self):
        def fake_stat(path):
            return SimpleNamespace(st_dev=1 if str(path) == '/' else 2)
        def fake_usage(path):
            return SimpleNamespace(free=(5 if str(path) == '/' else 43) * 1024**3)
        with patch.object(agent, 'run', return_value='/mnt/rosdata/docker'), \
             patch.object(agent.os, 'stat', side_effect=fake_stat), \
             patch.object(agent.shutil, 'disk_usage', side_effect=fake_usage):
            with self.assertRaisesRegex(agent.Stop, 'require >= 6 GiB'):
                agent.disk_check()

    def test_docker_on_root_still_requires_12_gib(self):
        def fake_stat(path):
            return SimpleNamespace(st_dev=1)
        def fake_usage(path):
            return SimpleNamespace(free=8 * 1024**3)
        with patch.object(agent, 'run', return_value='/var/lib/docker'), \
             patch.object(agent.os, 'stat', side_effect=fake_stat), \
             patch.object(agent.shutil, 'disk_usage', side_effect=fake_usage):
            with self.assertRaisesRegex(agent.Stop, 'require >= 12 GiB'):
                agent.disk_check()

    def test_separate_docker_disk_low_space_stops(self):
        def fake_stat(path):
            return SimpleNamespace(st_dev=1 if str(path) == '/' else 2)
        def fake_usage(path):
            return SimpleNamespace(free=(8 if str(path) == '/' else 10) * 1024**3)
        with patch.object(agent, 'run', return_value='/mnt/rosdata/docker'), \
             patch.object(agent.os, 'stat', side_effect=fake_stat), \
             patch.object(agent.shutil, 'disk_usage', side_effect=fake_usage):
            with self.assertRaisesRegex(agent.Stop, 'Insufficient Docker data-root disk'):
                agent.disk_check()

    def test_choose_valid_release(self):
        good={'name':'Publish and deploy verified release','event':'workflow_run','head_branch':'main','head_sha':'a'*40,'status':'completed','conclusion':'success','id':5}
        self.assertEqual(agent.choose_published_release('a'*40,[good]),good)
    def test_reject_failed_or_old_or_wrong_branch(self):
        good={'name':'Publish and deploy verified release','event':'workflow_run','head_branch':'main','head_sha':'a'*40,'status':'completed','conclusion':'success','id':5}
        for key,val in [('conclusion','failure'),('head_sha','b'*40),('head_branch','other'),('event','push'),('status','in_progress')]:
            bad={**good,key:val}
            self.assertIsNone(agent.choose_published_release('a'*40,[bad]))
    def test_paused_skips_all_network_and_docker(self):
        with patch.object(agent.PAUSE_FILE.__class__,'exists',return_value=True),patch.object(agent,'disk_check') as disk,patch.object(agent,'api_json') as api:
            agent.check_once()
            disk.assert_not_called(); api.assert_not_called()
    def test_docker_compose_only_web(self):
        with patch.object(agent,'compose') as c:
            agent.swap_web('ghcr.io/ratamon2606-create/freesize-web@sha256:'+'a'*64)
            self.assertEqual(c.call_args.args,('up','-d','--no-deps','--no-build','--pull','never','--force-recreate','web'))
    def test_no_enable_file_means_default_paused(self):
        with patch.object(agent.PAUSE_FILE.__class__, 'exists', return_value=False), \
             patch.object(agent, 'ENABLE_FILE') as enabled, \
             patch.object(agent, 'api_json') as api, \
             patch.object(agent, 'disk_check') as disk:
            enabled.exists.return_value = False
            agent.check_once()
            api.assert_not_called(); disk.assert_not_called()

    def test_bad_health_after_swap_attempts_rollback_and_pause(self):
        sha = 'a'*40
        with patch.object(agent.PAUSE_FILE.__class__, 'exists', return_value=False), \
             patch.object(agent, 'ENABLE_FILE') as enabled, \
             patch.object(agent, 'env_values', return_value={'PUBLIC_ORIGIN':'https://example.ts.net'}), \
             patch.object(agent, 'disk_check'), \
             patch.object(agent, 'published_sha', return_value=sha), \
             patch.object(agent, 'verify_merge_policy'), \
             patch.object(agent, 'get_state', return_value={}), \
             patch.object(agent, 'api_json', return_value={'commit':{'sha':sha}}), \
             patch.object(agent, 'run'), \
             patch.object(agent, 'image_digest_for', return_value=agent.IMAGE+'@sha256:'+'c'*64), \
             patch.object(agent, 'running_image_id', return_value='sha256:'+'b'*64), \
             patch.object(agent, 'swap_web') as swap, \
             patch.object(agent, 'ensure_paused') as pause, \
             patch.object(agent, 'save_state') as save, \
             patch.object(agent, 'assert_health', side_effect=[None,None,agent.Stop('bad new image'),None]):
            enabled.exists.return_value = True
            agent.check_once()
            self.assertEqual(swap.call_count,2)
            self.assertEqual(swap.call_args.args,(agent.BACKUP_TAG,))
            pause.assert_called_once()
            self.assertEqual(save.call_args.args[0]['blocked_sha'],sha)

    def test_review_zero_still_requires_ci(self):
        fake = {'enforcement':'active','conditions':{'ref_name':{'include':['~DEFAULT_BRANCH']}},'rules':[
            {'type':'required_signatures'},
            {'type':'pull_request','parameters':{'required_approving_review_count':0}},
            {'type':'required_status_checks','parameters':{'required_status_checks':[]}}]}
        with patch.object(agent, 'api_json', return_value=fake):
            with self.assertRaisesRegex(agent.Stop,'Missing one or more of 5 required CI checks'):
                agent.verify_merge_policy()

    def test_zero_review_with_required_ci_is_allowed(self):
        required = [
            "Unit and API (preview)",
            "Browser UI smoke",
            "Hybrid DB (PostgreSQL + MongoDB)",
            "Build container images",
            "Dependency vulnerability audit",
        ]
        fake = {
            "enforcement": "active",
            "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"]}},
            "rules": [
                {"type": "required_signatures"},
                {"type": "pull_request", "parameters": {
                    "required_approving_review_count": 0
                }},
                {"type": "required_status_checks", "parameters": {
                    "required_status_checks": [
                        {"context": name} for name in required
                    ]
                }},
            ],
        }
        with patch.object(agent, "api_json", return_value=fake), \
             patch.object(agent, "log"):
            agent.verify_merge_policy()

    def test_connection_reset_is_retryable(self):
        with patch.object(
            agent.urllib.request, "urlopen",
            side_effect=ConnectionResetError("peer reset")
        ):
            with self.assertRaisesRegex(agent.Stop, "ConnectionResetError"):
                agent.get_json("http://127.0.0.1:3100/api/ready")

    def test_health_recovers_after_transient_failure(self):
        ready = {
            "ready": True,
            "sql": "PostgreSQL",
            "nosql": "MongoDB",
        }
        responses = [
            agent.Stop("temporary connection reset"),
            ready,
            ready,
            {"demo_enabled": False, "invite_required": True},
        ]
        with patch.object(agent, "get_json", side_effect=responses) as getter:
            with patch.object(agent.time, "sleep") as sleeper:
                agent.assert_health(
                    {
                        "PUBLIC_ORIGIN": "https://example.ts.net",
                        "REQUIRE_BACKGROUND_AI": "0",
                    },
                    attempts=2,
                )
        self.assertEqual(getter.call_count, 4)
        sleeper.assert_called_once_with(3)

    def test_health_denies_demo(self):
        vals={'PUBLIC_ORIGIN':'https://example.ts.net','REQUIRE_BACKGROUND_AI':'0'}
        def get(url,timeout=6):
            if url.endswith('/api/health'):return {'demo_enabled':True,'invite_required':True}
            return {'ready':True,'sql':'PostgreSQL','nosql':'MongoDB'}
        with patch.object(agent,'get_json',side_effect=get):
            with self.assertRaises(agent.Stop):agent.assert_health(vals)

if __name__=='__main__':unittest.main()
