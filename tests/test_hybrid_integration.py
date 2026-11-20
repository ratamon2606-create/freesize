"""Genuine PostgreSQL + MongoDB smoke and rollback tests.
Run on a *disposable* test database only, via .github/workflows/ci.yml.
"""
import os, json
from pathlib import Path
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

pytestmark = pytest.mark.skipif(
    os.getenv('WARDROBE_TEST_HYBRID') != '1', reason='requires disposable CI PostgreSQL + MongoDB'
)


def test_hybrid_databases_privacy_outbox_and_rollback():
    import app as server
    from uuid import uuid4

    assert server.sql.pg and os.environ['MONGO_URL']
    # CI services start with an empty, disposable database.
    with TestClient(server.app) as a, TestClient(server.app) as b:
        health = a.get('/api/ready')
        assert health.status_code == 200, health.text
        assert health.json()['sql'] == 'PostgreSQL'
        assert health.json()['nosql'] == 'MongoDB'

        salt = uuid4().hex[:10]
        for c, username in ((a, f'ci_a_{salt}'), (b, f'ci_b_{salt}')):
            res = c.post('/api/auth/register', json={
                'username': username, 'email': username+'@example.test',
                'password': 'unique-ci-password-123', 'display_name': username,
            })
            assert res.status_code == 200, res.text

        user_a = a.get('/api/me').json()
        user_b = b.get('/api/me').json()
        friend = a.post('/api/friends', json={'username': user_b['username']})
        assert friend.status_code == 200, friend.text
        pending = b.get('/api/friends').json()['incoming'][0]
        assert b.patch('/api/friends/'+pending['friendship_id'],json={'action':'accept'}).status_code == 200
        # LIKE ... ESCAPE must treat "_" literally on PostgreSQL too.
        found = [u['username'] for u in a.get('/api/users/search', params={'q': f'ci_b_{salt}'}).json()['users']]
        assert found == [user_b['username']], found
        assert a.get('/api/users/search', params={'q': 'ci%b'}).json()['users'] == []

        buf = BytesIO(); Image.new('RGB',(50,60),'#89745c').save(buf,format='PNG')
        up = a.post('/api/wardrobe',data={'name':'CI coat','category':'Outerwear','color':'Brown','request_token':salt},
            files={'file':('coat.png',buf.getvalue(),'image/png')})
        assert up.status_code == 200, up.text
        cloth = a.get('/api/wardrobe').json()['items'][0]
        assert cloth['owner_id'] == user_a['id']

        outfit = b.post('/api/ootds',json={'title':'CI Collaboration','items':[{'item_id':cloth['id'],'x':0.15,'y':0.2,'w':0.5,'z':1}]})
        assert outfit.status_code == 200, outfit.text
        slot = outfit.json()['id']
        assert server.docs.db['outfit_compositions'].find_one({'slot_id':slot}) is not None
        assert any(o['id']==slot for o in b.get('/api/ootds').json()['ootds'])

        # Server-side OOTD privacy with three REAL SQL users and Mongo documents:
        # A is friends with B; B is friends with C; A is NOT friends with C.
        with TestClient(server.app) as c:
            c_name=f'ci_c_{salt}'
            assert c.post('/api/auth/register',json={'username':c_name,'email':c_name+'@example.test',
                'password':'unique-ci-password-123'}).status_code==200
            assert b.post('/api/friends',json={'username':c_name}).status_code==200
            inc=c.get('/api/friends').json()['incoming']
            assert c.patch('/api/friends/'+inc[0]['friendship_id'],json={'action':'accept'}).status_code==200
            pic=BytesIO();Image.new('RGB',(42,52),'#7d5d43').save(pic,format='PNG')
            other=c.post('/api/wardrobe',data={'name':'C private blazer','category':'Outerwear'},
                files={'file':('private.png',pic.getvalue(),'image/png')})
            assert other.status_code==200,other.text
            secret=b.post('/api/ootds',json={'title':'Not for A','items':[{'item_id':other.json()['id'],
                'x':.15,'y':.2,'w':.43,'z':1}]})
            assert secret.status_code==200,secret.text
            closet=a.get('/api/friends/'+user_b['id']+'/closet')
            assert closet.status_code==200,closet.text
            assert slot in [x['id'] for x in closet.json()['ootds']]
            assert secret.json()['id'] not in [x['id'] for x in closet.json()['ootds']]
            assert 'Not for A' not in closet.text

        req = b.post('/api/borrowing',json={'item_id':cloth['id']})
        assert req.status_code == 200, req.text
        request_id = req.json()['id']
        assert a.patch('/api/borrowing/'+request_id,json={'action':'approve'}).status_code == 200
        with server.sql.tx() as t:
            before = t.one('SELECT state,current_holder_id FROM clothing_items WHERE id=?',(cloth['id'],))
        assert before['state']=='reserved'

        # Genuine forced failure AFTER a database write; check the rollback.
        with pytest.raises(RuntimeError,match='CI forced rollback'):
            with server.sql.tx() as t:
                t.execute("UPDATE clothing_items SET state='borrowed' WHERE id=?",(cloth['id'],))
                t.insert('loans',{'id':uuid4().hex,'request_id':request_id,'item_id':cloth['id'],
                    'lender_id':user_a['id'],'borrower_id':user_b['id'],'status':'active'})
                raise RuntimeError('CI forced rollback')
        with server.sql.tx() as t:
            after=t.one('SELECT state,current_holder_id FROM clothing_items WHERE id=?',(cloth['id'],))
            loan=t.one('SELECT id FROM loans WHERE request_id=?',(request_id,))
        assert before==after and loan is None, 'Failed transaction partially committed!'
        proof={'engine':'PostgreSQL','database_mode':'real PostgreSQL + MongoDB',
               'step':'forced failure after UPDATE clothing_items + INSERT loans',
               'before':before,'after':after,'loan_after_failure':loan,
               'rollback_verified':before==after and loan is None}
        output=os.getenv('EVIDENCE_OUTPUT_DIR')
        if output:
            folder=Path(output);folder.mkdir(parents=True,exist_ok=True)
            (folder/'rollback-postgres.json').write_text(json.dumps(proof,indent=2))

        assert a.patch('/api/borrowing/'+request_id,json={'action':'handover'}).status_code == 200
        assert b.patch('/api/borrowing/'+request_id,json={'action':'request_return'}).status_code == 200
        assert a.patch('/api/borrowing/'+request_id,json={'action':'confirm_return'}).status_code == 200
        assert server.docs.db['borrow_status_logs'].count_documents({'request_id':request_id}) > 0
        assert server.docs.db['notifications'].count_documents({'user_id':user_a['id']}) > 0
        assert server.docs.db['activity_logs'].count_documents({'user_id':user_a['id']}) > 0
        # True MongoDB CRUD, driven by web API, not direct manual DB writes.
        reminder=a.post('/api/notifications',json={'title':'Gate reminder','message':'Return jacket'})
        assert reminder.status_code==200,reminder.text
        rem_id=reminder.json()['id']
        assert server.docs.db['notifications'].find_one({'id':rem_id}) is not None
        assert a.patch('/api/notifications/'+rem_id,json={'read':True}).status_code==200
        assert server.docs.db['notifications'].find_one({'id':rem_id})['read'] is True
        assert a.delete('/api/notifications/'+rem_id).status_code==200
        assert server.docs.db['notifications'].find_one({'id':rem_id}) is None
        note=a.post('/api/borrow-logs',json={'note':'Gate personal note'})
        assert note.status_code==200
        note_id=note.json()['id']
        assert a.patch('/api/borrow-logs/'+note_id,json={'note':'Updated note'}).status_code==200
        assert server.docs.db['borrow_status_logs'].find_one({'id':note_id})['note']=='Updated note'
        assert a.delete('/api/borrow-logs/'+note_id).status_code==200
        # Test SQL-outbox delivery recovery when a Mongo insert fails once.
        original=server.docs.put
        def failing_once(name,obj,match=None):
            if name=='activity_logs':
                raise RuntimeError('simulated Mongo write failure')
            return original(name,obj,match)
        server.docs.put=failing_once
        with server.sql.tx() as t:
            eid=server.UID()
            t.insert('outbox_events',{'id':eid,'event_type':'activity',
                'payload_json':json.dumps({'user_id':user_a['id'],'action':'retry_gate','message':'eventually delivered'}),
                'created_at':server.STAMP()})
        server.process_outbox()
        with server.sql.tx() as t:pending=t.one('SELECT * FROM outbox_events WHERE id=?',(eid,))
        assert pending['processed_at'] is None and pending['attempts']>=1
        server.docs.put=original
        server.process_outbox()
        with server.sql.tx() as t:done=t.one('SELECT * FROM outbox_events WHERE id=?',(eid,))
        assert done['processed_at'] is not None
        assert server.docs.db['activity_logs'].find_one({'id':eid}) is not None
        if output:
            (Path(output)/'nosql-integration.json').write_text(json.dumps({
                'db_engine':'MongoDB','collections':server.docs.db.list_collection_names(),
                'borrow_status_events':server.docs.db['borrow_status_logs'].count_documents({'request_id':request_id}),
                'outbox_retry_attempts':done['attempts'], 'delivered_after_retry':bool(done['processed_at'])},indent=2))
