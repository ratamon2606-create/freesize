"""Run with: python -m pytest tests -q (SQLite+JSON preview mode).
The same route-level privacy and borrowing invariants are used in PostgreSQL mode.
"""
import os, sys, importlib, tempfile
from pathlib import Path
from fastapi.testclient import TestClient

def fresh_app():
    work=tempfile.TemporaryDirectory();os.environ.pop('DATABASE_URL',None);os.environ.pop('MONGO_URL',None)
    os.environ['DATA_DIR']=work.name;os.environ['SEED_DEMO']='1'
    if 'app' in sys.modules:del sys.modules['app']
    app=importlib.import_module('app')
    return work,app

def signin(client,username):
    resp=client.post('/api/auth/login',json={'username':username,'password':'demo12345'})
    assert resp.status_code==200,resp.text
    return client.get('/api/me').json()

def test_privacy_and_ootd_persistence():
    work,server=fresh_app()
    with TestClient(server.app) as client:
        taa=signin(client,'taa')
        friends=client.get('/api/friends').json()['friends']
        may=next(f for f in friends if f['username']=='may')
        closet=client.get('/api/friends/'+may['id']+'/closet').json()
        titles=[x['title'] for x in closet['ootds']]
        assert titles==['Library Day'],f'Do not expose private mixed-friend outfits: {titles}'
        assert closet['user']['ootds_count']==1
        friend_item=closet['items'][0]
        r=client.post('/api/ootds',json={'title':'Our Look ♡','items':[{'item_id':friend_item['id'],'x':.2,'y':.3,'w':.5,'z':1}]})
        assert r.status_code==200,r.text
        ident=r.json()['id']
        o=next(x for x in client.get('/api/ootds').json()['ootds'] if x['id']==ident)
        assert o['items'][0]['owner_name']=='May'
        assert client.delete('/api/ootds/'+ident).status_code==200
        assert all(x['id']!=ident for x in client.get('/api/ootds').json()['ootds'])
    work.cleanup()

def test_borrow_handover_and_return_transaction():
    work,server=fresh_app()
    with TestClient(server.app) as borrower, TestClient(server.app) as owner:
        taa=signin(borrower,'taa');may=signin(owner,'may')
        item=owner.get('/api/wardrobe').json()['items'][0]
        r=borrower.post('/api/borrowing',json={'item_id':item['id']});assert r.status_code==200,r.text
        rid=r.json()['id']
        r=owner.patch('/api/borrowing/'+rid,json={'action':'approve'});assert r.status_code==200,r.text
        assert owner.get('/api/wardrobe').json()['items'][0]['state']=='reserved'
        # Another request for same item cannot be approved once reserved.
        r=owner.patch('/api/borrowing/'+rid,json={'action':'handover'});assert r.status_code==200,r.text
        state=owner.get('/api/wardrobe').json()['items'][0]
        assert state['state']=='borrowed' and state['current_holder_id']==taa['id']
        assert borrower.patch('/api/borrowing/'+rid,json={'action':'request_return'}).status_code==200
        assert owner.patch('/api/borrowing/'+rid,json={'action':'confirm_return'}).status_code==200
        state=owner.get('/api/wardrobe').json()['items'][0]
        assert state['state']=='available' and not state['current_holder_id']
        assert owner.get('/api/activity').json()['borrow_logs']
    work.cleanup()

def test_permissions_and_journal_crud():
    work,server=fresh_app()
    with TestClient(server.app) as c:
        user=signin(c,'taa')
        wardrobe=c.get('/api/wardrobe').json()['items']
        with TestClient(server.app) as mayclient:
            signin(mayclient,'may')
            may_item=mayclient.get('/api/wardrobe').json()['items'][0]
        assert c.patch('/api/wardrobe/'+may_item['id'],json={'name':'Hacked'}).status_code==403
        assert c.delete('/api/wardrobe/'+may_item['id']).status_code==403
        assert c.get('/api/friends/someunknownid/closet').status_code==403
        r=c.post('/api/activity',json={'note':'Test a new style tomorrow'});assert r.status_code==200
        aid=r.json()['id']
        assert c.patch('/api/activity/'+aid,json={'note':'Maybe next week'}).status_code==200
        assert c.delete('/api/activity/'+aid).status_code==200
        # Everyone's avatars can be visible in search, but private media is protected.
        assert c.get('/media/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png').status_code in (403,404)
    work.cleanup()

def test_reserved_item_cannot_be_approved_twice_and_rolls_back():
    work,server=fresh_app()
    with TestClient(server.app) as taa_client, TestClient(server.app) as june_client, TestClient(server.app) as may_client:
        taa=signin(taa_client,'taa');june=signin(june_client,'june');may=signin(may_client,'may')
        # May and June first establish friendship; Taa is already May's friend.
        r=may_client.post('/api/friends',json={'username':'june'}); assert r.status_code==200,r.text
        inc=june_client.get('/api/friends').json()['incoming']
        request_id=next(x['friendship_id'] for x in inc if x['username']=='may')
        assert june_client.patch('/api/friends/'+request_id,json={'action':'accept'}).status_code==200
        item=may_client.get('/api/wardrobe').json()['items'][0]
        r1=taa_client.post('/api/borrowing',json={'item_id':item['id']})
        r2=june_client.post('/api/borrowing',json={'item_id':item['id']})
        assert r1.status_code==200 and r2.status_code==200
        assert may_client.patch('/api/borrowing/'+r1.json()['id'],json={'action':'approve'}).status_code==200
        denied=may_client.patch('/api/borrowing/'+r2.json()['id'],json={'action':'approve'})
        assert denied.status_code==409,denied.text
        active=may_client.get('/api/wardrobe').json()['items'][0]
        assert active['state']=='reserved' and active['current_holder_id'] is None
        reqs=may_client.get('/api/borrowing').json()['requests']
        assert next(x for x in reqs if x['id']==r1.json()['id'])['status']=='approved'
        assert next(x for x in reqs if x['id']==r2.json()['id'])['status']=='rejected'
        # The second borrower should be told why their request is no longer pending.
        updates=june_client.get('/api/notifications').json()['notifications']
        assert any('reserved for another borrower' in n['message'] for n in updates)
    work.cleanup()


def test_register_and_upload_clothing_with_media_permissions():
    from io import BytesIO
    from PIL import Image
    work,server=fresh_app()
    with TestClient(server.app) as c:
        r=c.post('/api/auth/register',json={'username':'newcloset','email':'newcloset@example.org','password':'longpassword123','display_name':'New Closet'})
        assert r.status_code==200,r.text
        buf=BytesIO();Image.new('RGB',(55,60),'#eee1d4').save(buf,format='PNG')
        r=c.post('/api/wardrobe',data={'name':'Test Blouse','category':'Tops','color':'Cream','details':'lovely'},files={'file':('test.png',buf.getvalue(),'image/png')})
        assert r.status_code==200,r.text
        assert r.json()['ai_processed'] is False  # no AI worker in preview mode
        file_url=r.json()['image_path'];assert c.get(file_url).status_code==200
        with TestClient(server.app) as stranger:
            signin(stranger,'leo')
            assert stranger.get(file_url).status_code==403
        item=c.get('/api/wardrobe').json()['items'][0]
        assert c.patch('/api/wardrobe/'+item['id'],json={'name':'Updated Blouse'}).status_code==200
        assert c.delete('/api/wardrobe/'+item['id']).status_code==200
        assert c.get('/api/wardrobe').json()['items']==[]
    work.cleanup()


def test_upload_retry_same_token_does_not_create_duplicate_or_event():
    """Double-clicking Save or retrying a timed-out request keeps one clothing row."""
    from io import BytesIO
    from PIL import Image
    work, server = fresh_app()
    with TestClient(server.app) as c:
        signin(c,'taa')
        count_before = len(c.get('/api/wardrobe').json()['items'])
        buf = BytesIO()
        Image.new('RGB',(34,46),'#b79d7b').save(buf,format='PNG')
        payload = buf.getvalue()
        params = {'name':'Unique Plaid Vest','category':'Tops','color':'Brown','request_token':'upload-test-one'}
        first = c.post('/api/wardrobe',data=params,files={'file':('first.png',payload,'image/png')})
        second = c.post('/api/wardrobe',data=params,files={'file':('second.png',payload,'image/png')})
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert first.json()['id'] == second.json()['id']
        assert first.json()['duplicate'] is False
        assert second.json()['duplicate'] is True
        clothing = c.get('/api/wardrobe').json()['items']
        assert len(clothing) == count_before + 1
        assert [i['name'] for i in clothing].count('Unique Plaid Vest') == 1
        # A distinct new upload still creates a new item.
        params['request_token'] = 'upload-test-two'
        third = c.post('/api/wardrobe',data=params,files={'file':('third.png',payload,'image/png')})
        assert third.status_code == 200
        assert third.json()['id'] != first.json()['id']
        assert len(c.get('/api/wardrobe').json()['items']) == count_before + 2
    work.cleanup()


def test_static_wardrobe_cards_expose_all_owner_actions():
    """Regression: owners need OOTD, Edit and Delete actions on every card."""
    from pathlib import Path
    js=(Path(__file__).parents[1]/'static/js/app.js').read_text()
    assert "case'addOwnToOOTD':" in js
    assert "btn(ico('edit')+' Edit','editClothing',i.id" in js
    assert "btn(ico('trash')+' Delete','deleteClothing',i.id" in js
    assert "if(f.dataset.submitting==='true')return;" in js


def test_full_document_collections_crud_and_audit_guard():
    """Every Mongo business collection has Create/Read/Update/Delete through UI APIs.
    System audit entries stay immutable; editable personal entries can be removed.
    """
    work,server=fresh_app()
    with TestClient(server.app) as c:
        signin(c,'taa')
        note=c.post('/api/borrow-logs',json={'note':'Try a jacket next week'})
        assert note.status_code==200,note.text
        nid=note.json()['id']
        assert any(x['id']==nid for x in c.get('/api/activity').json()['borrow_logs'])
        assert c.patch('/api/borrow-logs/'+nid,json={'note':'Returned on Friday'}).status_code==200
        assert c.delete('/api/borrow-logs/'+nid).status_code==200
        assert not any(x['id']==nid for x in c.get('/api/activity').json()['borrow_logs'])
        # Outbox-generated logs are available but protected from deletion.
        # Generate one system-owned audit log through a real borrowing request.
        with TestClient(server.app) as may:
            signin(may,'may')
            item=may.get('/api/wardrobe').json()['items'][0]
        assert c.post('/api/borrowing',json={'item_id':item['id']}).status_code==200
        logs=c.get('/api/activity').json()['borrow_logs']
        assert logs
        assert c.delete('/api/borrow-logs/'+logs[0]['id']).status_code==403
        reminder=c.post('/api/notifications',json={'title':'Wash coat','message':'Before lending it'})
        assert reminder.status_code==200,reminder.text
        rid=reminder.json()['id']
        assert any(x['id']==rid for x in c.get('/api/notifications').json()['notifications'])
        assert c.patch('/api/notifications/'+rid,json={'read':True}).status_code==200
        assert c.delete('/api/notifications/'+rid).status_code==200
        assert not any(x['id']==rid for x in c.get('/api/notifications').json()['notifications'])
    work.cleanup()


def test_terminal_loan_delete_and_account_closure_are_atomic_and_private():
    work,server=fresh_app()
    with TestClient(server.app) as borrow, TestClient(server.app) as owner:
        borrower=signin(borrow,'taa'); lender=signin(owner,'may')
        item=owner.get('/api/wardrobe').json()['items'][0]
        rid=borrow.post('/api/borrowing',json={'item_id':item['id']}).json()['id']
        assert borrow.delete('/api/borrowing/'+rid).status_code==409
        assert owner.patch('/api/borrowing/'+rid,json={'action':'approve'}).status_code==200
        assert owner.patch('/api/borrowing/'+rid,json={'action':'handover'}).status_code==200
        assert borrow.delete('/api/borrowing/'+rid).status_code==409
        assert borrow.delete('/api/me').status_code==409  # active loan prevents account closure
        assert borrow.patch('/api/borrowing/'+rid,json={'action':'request_return'}).status_code==200
        assert owner.patch('/api/borrowing/'+rid,json={'action':'confirm_return'}).status_code==200
        assert borrow.delete('/api/borrowing/'+rid).status_code==200
        with server.sql.tx() as t:
            assert t.one('SELECT id FROM loans WHERE request_id=?',(rid,)) is None
            assert t.one('SELECT id FROM borrow_requests WHERE id=?',(rid,)) is None
        assert borrow.delete('/api/me').status_code==200
        assert borrow.get('/api/me').status_code==401
        assert owner.get('/api/users/search?q=taa').json()['users']==[]
        with server.sql.tx() as t:
            user=t.one('SELECT * FROM users WHERE id=?',(borrower['id'],))
            assert user['password_hash']=='!deleted!' and user['avatar_path'] is None
        assert borrow.post('/api/auth/login',json={'username':'taa','password':'demo12345'}).status_code==401
    work.cleanup()


def test_forced_mid_transaction_failure_restores_rows_and_events():
    import pytest
    work,server=fresh_app()
    with TestClient(server.app) as c:
        user=signin(c,'taa')
        item=c.get('/api/wardrobe').json()['items'][0]
        with server.sql.tx() as t:
            prior=t.one('SELECT state,current_holder_id FROM clothing_items WHERE id=?',(item['id'],))
            before=t.one('SELECT COUNT(*) AS n FROM outbox_events')['n']
        with pytest.raises(RuntimeError,match='injected error'):
            with server.sql.tx() as t:
                t.execute("UPDATE clothing_items SET state='reserved' WHERE id=?",(item['id'],))
                server.event(t,'activity',user_id=user['id'],action='forced_failure',message='should never appear')
                raise RuntimeError('injected error')
        with server.sql.tx() as t:
            after=t.one('SELECT state,current_holder_id FROM clothing_items WHERE id=?',(item['id'],))
            count=t.one('SELECT COUNT(*) AS n FROM outbox_events')['n']
        assert after==prior and count==before
        assert not any(e['action']=='forced_failure' for e in c.get('/api/activity').json()['activity'])
    work.cleanup()



def test_required_local_ai_model_fails_readiness_before_production(monkeypatch):
    """Missing-model behavior must be independent of the developer's models/ dir.

    Developers can have u2netp.onnx installed locally; the disposable gate
    mounts the project source into /src. Explicitly simulate an absent model
    instead of relying on the host's model directory being empty.
    """
    work,server=fresh_app()
    try:
        with TestClient(server.app) as c:
            assert c.get('/api/ready').status_code == 200
            monkeypatch.setattr(server, 'REQUIRE_BACKGROUND_AI', True)
            monkeypatch.setattr(server, 'MODEL_FILE', Path(work.name) / 'missing-u2netp.onnx')
            res=c.get('/api/ready')
            assert res.status_code == 503 and not res.json()['ready']
            assert 'model missing' in ' '.join(res.json()['problems'])
    finally:
        work.cleanup()


def test_profile_email_password_crud_and_permission_checks():
    work,server=fresh_app()
    with TestClient(server.app) as c:
        signin(c,'taa')
        assert c.patch('/api/me',json={'email':'newtaa@example.test'}).status_code==200
        assert c.get('/api/me').json()['email']=='newtaa@example.test'
        wrong=c.patch('/api/me',json={'current_password':'incorrect','new_password':'newsecret123'})
        assert wrong.status_code==403
        assert c.patch('/api/me',json={'current_password':'demo12345','new_password':'newsecret123'}).status_code==200
        assert c.post('/api/auth/logout').status_code==200
        assert c.post('/api/auth/login',json={'username':'taa','password':'demo12345'}).status_code==401
        assert c.post('/api/auth/login',json={'username':'newtaa@example.test','password':'newsecret123'}).status_code==200
    work.cleanup()
