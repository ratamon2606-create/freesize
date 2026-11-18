"""Closed-beta security tests (do not need Docker or network access)."""
import importlib
import os
import subprocess
import sys
import tempfile
from io import BytesIO
from fastapi.testclient import TestClient
from PIL import Image


def isolated_app(*, invite=''):
    temp = tempfile.TemporaryDirectory()
    os.environ.pop('DATABASE_URL', None)
    os.environ.pop('MONGO_URL', None)
    os.environ.pop('COOKIE_SECURE', None)
    os.environ['SEED_DEMO'] = '0'
    os.environ['DATA_DIR'] = temp.name
    if invite:
        os.environ['REGISTRATION_INVITE_CODE'] = invite
    else:
        os.environ.pop('REGISTRATION_INVITE_CODE', None)
    sys.modules.pop('app', None)
    return temp, importlib.import_module('app')


def test_invite_only_registration_and_no_seed():
    code = 'example-private-code-should-be-random-12345'
    temp, server = isolated_app(invite=code)
    try:
        with TestClient(server.app) as client:
            config = client.get('/api/health').json()
            assert config['invite_required'] is True
            assert config['demo_enabled'] is False
            assert client.post('/api/auth/login', json={'username':'taa','password':'demo12345'}).status_code == 401
            details={'username':'member1','email':'member1@example.com','password':'goodpassword123'}
            assert client.post('/api/auth/register', json=details).status_code == 403
            assert client.post('/api/auth/register', json={**details,'invite_code':'wrong'}).status_code == 403
            success=client.post('/api/auth/register', json={**details,'invite_code':code})
            assert success.status_code == 200, success.text
            assert client.get('/api/me').json()['username']=='member1'
    finally:
        os.environ.pop('REGISTRATION_INVITE_CODE',None)
        temp.cleanup()


def test_login_rate_limit_rejects_repeated_guesses():
    temp, server = isolated_app()
    try:
        with TestClient(server.app) as client:
            for i in range(12):
                r=client.post('/api/auth/login',json={'username':'nonexistent','password':'wrong'})
                assert r.status_code==401,(i,r.text)
            assert client.post('/api/auth/login',json={'username':'nonexistent','password':'wrong'}).status_code==429
    finally:
        temp.cleanup()


def test_reject_disguised_nonimage_upload():
    temp, server = isolated_app()
    try:
        with TestClient(server.app) as client:
            resp=client.post('/api/auth/register',json={'username':'firstuser','email':'firstuser@example.com','password':'supersecurepassword'})
            assert resp.status_code==200
            fake=client.post('/api/wardrobe',data={'name':'not a real image','category':'Tops'},files={'file':('attack.png',b'<svg onload="alert(1)"></svg>','image/png')})
            assert fake.status_code in (400,415),fake.text
    finally:
        temp.cleanup()


def test_public_production_fails_closed_without_database_and_invite():
    env=os.environ.copy()
    env.update({'COOKIE_SECURE':'1','PUBLIC_ORIGIN':'https://closet.example.org','SEED_DEMO':'0','REGISTRATION_INVITE_CODE':'','DATABASE_URL':'','MONGO_URL':''})
    result=subprocess.run([sys.executable,'-c','import app'],env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    assert result.returncode!=0
    assert 'Production requires both PostgreSQL and MongoDB' in result.stderr


def test_production_rejects_example_invitation_code():
    env=os.environ.copy()
    env.update({'COOKIE_SECURE':'1','PUBLIC_ORIGIN':'https://closet.example.org',
                'SEED_DEMO':'0','REGISTRATION_INVITE_CODE':'REPLACE_THIRD_RANDOM_64_HEX_CHARS',
                'DATABASE_URL':'postgresql://wardrobe:x@postgres:5432/wardrobeshare',
                'MONGO_URL':'mongodb://user:pass@mongo:27017/'})
    result=subprocess.run([sys.executable,'-c','import app'],env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    assert result.returncode != 0
    assert 'random hex' in result.stderr


def test_production_source_config_guards():
    from pathlib import Path
    code=Path('app.py').read_text()
    compose=Path('deploy/compose.production.yml').read_text()
    assert 'REGISTRATION_INVITE_CODE' in code
    assert "'no-new-privileges:true'" in compose
    assert "SEED_DEMO: '0'" in compose
    assert "COOKIE_SECURE: '1'" in compose
    assert "REQUIRE_BACKGROUND_AI: '1'" in compose
    assert "- '80:80'" in compose and "- '443:443'" in compose
    assert '/app/data' in compose
