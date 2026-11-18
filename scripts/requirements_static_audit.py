#!/usr/bin/env python3
"""Fail-closed source/diagram/static evidence audit. NOT a runtime compliance certificate."""
import ast, json, re, sys
from pathlib import Path
root=Path(__file__).resolve().parent.parent
source=(root/'app.py').read_text()
ui=(root/'static/js/app.js').read_text()
compose=(root/'docker-compose.yml').read_text()
production=(root/'deploy/compose.production.yml').read_text()
index=(root/'static/index.html').read_text()
assert ast.parse(source)


def no_demo_db_in_git_candidate():
    """Check files that Git would actually include, not local ignored runtime files."""
    import shutil
    import subprocess
    import tempfile

    protected = ('.env', '.env.local', '.env.production',
                 'data/wardrobe.sqlite3', 'data/documents.json',
                 'models/u2netp.onnx', 'backups/backup.zip', 'state/deploy-state.json')

    def sensitive(path):
        return (
            path.startswith(('data/', 'models/', 'backups/', 'state/'))
            or path == '.env'
            or (path.startswith('.env.')
                and path not in ('.env.example', '.env.production.example'))
            or path.endswith(('.pem', '.key'))
        )
    where = subprocess.run(
        ['git', '-C', str(root), 'rev-parse', '--show-toplevel'],
        capture_output=True, text=True, check=False,
    )
    if where.returncode == 0:
        repo_root = Path(where.stdout.strip()).resolve()
        prefix = root.resolve().relative_to(repo_root)
        listed = subprocess.run(
            ['git', '-C', str(repo_root), 'ls-files', '--cached', '--others',
             '--exclude-standard', '-z'],
            capture_output=True, check=True,
        ).stdout.decode('utf-8').split('\0')
        for path in listed:
            if not path:
                continue
            try:
                relative = Path(path).relative_to(prefix).as_posix()
            except ValueError:
                continue
            if sensitive(relative):
                print('UNSAFE Git candidate includes:', relative)
                return False
        return True

    # Before git init, verify the ignore rules using a disposable Git repository.
    # Local .env and database files are allowed as long as Git will ignore them.
    with tempfile.TemporaryDirectory(prefix='wardrobeshare-gitignore-') as directory:
        temp = Path(directory)
        shutil.copyfile(root / '.gitignore', temp / '.gitignore')
        subprocess.run(['git', '-C', str(temp), 'init', '-q'], check=True)
        for path in protected:
            check = subprocess.run(
                ['git', '-C', str(temp), 'check-ignore', '--no-index', '-q', '--', path],
                check=False,
            )
            if check.returncode != 0:
                print('UNSAFE .gitignore does not exclude:', path)
                return False
    return True

checks={}
checks['9_sql_tables']=len(re.findall(r'CREATE TABLE IF NOT EXISTS (\w+)',source))==9
checks['4_nosql_collections']=all(k in source for k in ('outfit_compositions','notifications','activity_logs','borrow_status_logs'))
checks['postgres_mongo_configured']=all(t in compose for t in ('postgres:16','mongo:7','DATABASE_URL','MONGO_URL'))
checks['local_image_paths']=all(t in source for t in ('DATA/\'uploads\'','@app.get(\'/media/{filename}\')'))
checks['no_external_runtime_assets']=not re.search(r'https?://',ui+index+(root/'static/css/app.css').read_text())
checks['no_ai_microservice']=not any(t in compose+production for t in ('background-ai:', 'BACKGROUND_REMOVE_URL:', 'AI_IMAGE:'))
checks['inprocess_ai_model']=all(t in source for t in ('from rembg import new_session, remove','MODEL_FILE.is_file()','U2NET_HOME'))
checks['transaction_rollback']=all(t in source for t in ('c.commit()','c.rollback()','outbox_events','process_outbox'))
checks['privacy_all_owners']=all(t in source for t in ('any(it.get(\'owner_id\') not in allowed for it in itemrefs)','if viewer_id!=user_id'))
checks['ui_crud_business']=all(t in ui for t in ('id="clothing-form"','id="reminder-form"','id="borrow-log-form"','id="journal-form"','deleteBorrowHistory','deleteAccount','saveOOTD'))
checks['diagram_png']=all((root/'docs/diagrams'/f).is_file() for f in ('architecture.png','sql_erd.png','nosql_schema.png'))
checks['no_demo_db_in_git_candidate']=no_demo_db_in_git_candidate()
checks['prepush_integration_test']=all((root/f).is_file() for f in ('docker-compose.gate.yml','scripts/prepush_gate.sh','tests/test_hybrid_integration.py'))
for k,ok in checks.items():print(('PASS' if ok else 'FAIL'),k)
print('Static checks passed:',sum(checks.values()),'/',len(checks))
sys.exit(0 if all(checks.values()) else 1)
