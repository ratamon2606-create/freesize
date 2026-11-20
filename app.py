"""WardrobeShare - self-hosted hybrid SQL/NoSQL wardrobe web app.

Without DATABASE_URL and MONGO_URL this app uses a persistent SQLite+JSON
preview. Docker Compose configures PostgreSQL and MongoDB for course submission.
"""
from __future__ import annotations
import asyncio, base64, contextlib, hashlib, hmac, io, json, os, re, secrets, sqlite3, threading, time, uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from fastapi import FastAPI, Request, Response, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent
DATA = Path(os.getenv('DATA_DIR', str(ROOT/'data')))
DATA.mkdir(parents=True, exist_ok=True)
(DATE_MEDIA := DATA/'uploads').mkdir(parents=True,exist_ok=True)
SQL_URL = os.getenv('DATABASE_URL','')
MONGO_URL = os.getenv('MONGO_URL','')
# Optional in-process segmentation. No external service or HTTP microservice.
AI_MODEL_DIR = Path(os.getenv('U2NET_HOME', str(ROOT/'models')))
REQUIRE_BACKGROUND_AI = os.getenv('REQUIRE_BACKGROUND_AI','0') == '1'
MODEL_FILE = AI_MODEL_DIR/'u2netp.onnx'
os.environ.setdefault('U2NET_HOME',str(AI_MODEL_DIR))
_ai_session = None
_ai_lock = threading.Lock()
COOKIE_NAME='ws_session'
IS_PROD=os.getenv('COOKIE_SECURE','0')=='1'
MAX_BETA_USERS = int(os.getenv('MAX_BETA_USERS','0') or '0')
if IS_PROD:
    if not SQL_URL or not MONGO_URL:
        raise RuntimeError('Production requires both PostgreSQL and MongoDB; preview storage is forbidden')
    if os.getenv('SEED_DEMO', '1') != '0':
        raise RuntimeError('Production requires SEED_DEMO=0')

# Single-Uvicorn-worker limits for closed beta. For horizontal scaling use a
# shared limiter in SQL/Redis or at the proxy; never claim distributed limits.
_rate_lock = threading.Lock()
_rate_events: dict[str, list[float]] = {}
def rate_limit(key: str, max_events: int, window_seconds: int):
    now = time.monotonic()
    with _rate_lock:
        if len(_rate_events) > 10000:
            for k, times in list(_rate_events.items()):
                remaining = [t for t in times if now-t < 3600]
                if remaining: _rate_events[k] = remaining
                else: _rate_events.pop(k, None)
        events = [t for t in _rate_events.get(key, []) if now-t < window_seconds]
        if len(events) >= max_events:
            raise HTTPException(status_code=429, detail='Too many attempts. Please try again later.', headers={'Retry-After': str(window_seconds)})
        events.append(now)
        _rate_events[key] = events

def source_key(req:Request):
    # We never trust X-Forwarded-For supplied by an arbitrary client.
    return str(req.client.host if req.client else 'unknown')

CATEGORIES=('Tops','Bottoms','Dresses','Outerwear','Shoes','Bags','Accessories','Other')
STAMP=lambda: datetime.now(timezone.utc).isoformat()
UID=lambda: uuid.uuid4().hex

SQL_SCHEMA = '''
CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, display_name TEXT NOT NULL, avatar_path TEXT, bio TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (id_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS clothing_items (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id), name TEXT NOT NULL, category TEXT NOT NULL, color TEXT NOT NULL DEFAULT '', details TEXT NOT NULL DEFAULT '', image_path TEXT NOT NULL, original_path TEXT, current_holder_id TEXT REFERENCES users(id), state TEXT NOT NULL DEFAULT 'available', ai_processed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, deleted_at TEXT);
CREATE TABLE IF NOT EXISTS clothing_upload_requests (user_id TEXT NOT NULL REFERENCES users(id), request_token TEXT NOT NULL, clothing_id TEXT NOT NULL REFERENCES clothing_items(id), created_at TEXT NOT NULL, PRIMARY KEY(user_id,request_token));
CREATE TABLE IF NOT EXISTS friendships (id TEXT PRIMARY KEY, requester_id TEXT NOT NULL REFERENCES users(id), addressee_id TEXT NOT NULL REFERENCES users(id), status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(requester_id, addressee_id));
CREATE TABLE IF NOT EXISTS outfit_slots (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id), title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS borrow_requests (id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES clothing_items(id), owner_id TEXT NOT NULL REFERENCES users(id), borrower_id TEXT NOT NULL REFERENCES users(id), status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS loans (id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE REFERENCES borrow_requests(id), item_id TEXT NOT NULL REFERENCES clothing_items(id), lender_id TEXT NOT NULL REFERENCES users(id), borrower_id TEXT NOT NULL REFERENCES users(id), status TEXT NOT NULL, started_at TEXT, ended_at TEXT);
CREATE TABLE IF NOT EXISTS outbox_events (id TEXT PRIMARY KEY, event_type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL, processed_at TEXT, attempts INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_clothing_owner ON clothing_items(owner_id);
CREATE INDEX IF NOT EXISTS idx_outfit_owner ON outfit_slots(owner_id);
CREATE INDEX IF NOT EXISTS idx_borrow_owner ON borrow_requests(owner_id);
CREATE INDEX IF NOT EXISTS idx_borrow_borrower ON borrow_requests(borrower_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_reserved_item ON borrow_requests(item_id) WHERE status IN ('approved','borrowed','return_requested');
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_loan ON loans(item_id) WHERE status='active';
'''

class SQL:
    def __init__(self):
        self.pg=bool(SQL_URL)
        self.lock=threading.RLock()
    @contextlib.contextmanager
    def tx(self):
        with self.lock:
            if self.pg:
                from psycopg import connect
                from psycopg.rows import dict_row
                c=connect(SQL_URL, row_factory=dict_row)
            else:
                c=sqlite3.connect(DATA/'wardrobe.sqlite3',timeout=15,check_same_thread=False)
                c.row_factory=sqlite3.Row
                c.execute('PRAGMA foreign_keys=ON')
            t=Tx(c,self.pg)
            try:
                yield t
                c.commit()
            except Exception:
                c.rollback();raise
            finally:
                c.close()
    def setup(self):
        with self.tx() as t:
            for statement in SQL_SCHEMA.split(';'):
                if statement.strip():t.execute(statement)

class Tx:
    def __init__(self,c,pg):self.c,self.pg=c,pg
    def execute(self,q,params=()):
        if self.pg:q=q.replace('?','%s')
        return self.c.execute(q,tuple(params))
    def all(self,q,params=()):return [dict(r) for r in self.execute(q,params).fetchall()]
    def one(self,q,params=()):
        r=self.execute(q,params).fetchone()
        return dict(r) if r is not None else None
    def insert(self,table,values):
        cols=list(values)
        self.execute(f'INSERT INTO {table} ({",".join(cols)}) VALUES ({",".join("?" for _ in cols)})',list(values.values()))

def event(t:Tx,kind:str,**payload):
    t.insert('outbox_events',{'id':UID(),'event_type':kind,'payload_json':json.dumps(payload,ensure_ascii=False),'created_at':STAMP()})

class Docs:
    """Mongo collections in production, JSON document emulation in preview mode."""
    names=('outfit_compositions','notifications','activity_logs','borrow_status_logs')
    def __init__(self):
        self.real=bool(MONGO_URL);self.lock=threading.RLock()
        if self.real:
            from pymongo import MongoClient
            self.client=MongoClient(MONGO_URL,serverSelectionTimeoutMS=5000)
            self.db=self.client['wardrobeshare']
            self.db['outfit_compositions'].create_index('slot_id',unique=True)
            for name in ('notifications','activity_logs','borrow_status_logs'):
                self.db[name].create_index('user_id')
        else:
            self.path=DATA/'documents.json'
            if not self.path.exists():self.path.write_text(json.dumps({name:[] for name in self.names}))
    def read(self,name,filters=None):
        filters=filters or {}
        with self.lock:
            if self.real:
                return [{**{k:v for k,v in obj.items() if k!='_id'},'id':obj.get('id') or str(obj['_id'])} for obj in self.db[name].find(filters)]
            items=json.loads(self.path.read_text()).get(name,[])
            return [dict(d) for d in items if all(d.get(k)==v for k,v in filters.items())]
    def put(self,name,obj,match=None):
        with self.lock:
            if self.real:
                if match:self.db[name].replace_one(match,obj,upsert=True)
                else:self.db[name].insert_one(obj)
            else:
                data=json.loads(self.path.read_text()); items=data[name]
                if match:
                    items[:]=[d for d in items if not all(d.get(k)==v for k,v in match.items())]
                items.append(obj)
                self.path.write_text(json.dumps(data,ensure_ascii=False))
    def patch(self,name,filters,fields):
        with self.lock:
            if self.real:return self.db[name].update_one(filters,{'$set':fields}).matched_count
            data=json.loads(self.path.read_text());matched=0
            for obj in data[name]:
                if all(obj.get(k)==v for k,v in filters.items()):obj.update(fields);matched+=1;break
            self.path.write_text(json.dumps(data,ensure_ascii=False));return matched
    def delete(self,name,filters):
        with self.lock:
            if self.real:return self.db[name].delete_many(filters).deleted_count
            data=json.loads(self.path.read_text()); n=len(data[name]);data[name]=[d for d in data[name] if not all(d.get(k)==v for k,v in filters.items())]
            self.path.write_text(json.dumps(data,ensure_ascii=False));return n-len(data[name])

sql=SQL()
docs:Docs|None=None

# Safe to retry; it is committed within SQL transaction before Mongo writes.
def process_outbox(limit=50):
    if docs is None:return
    with sql.tx() as t:
        pending=t.all('SELECT * FROM outbox_events WHERE processed_at IS NULL ORDER BY created_at LIMIT ?', (limit,))
    for e in pending:
        try:
            p=json.loads(e['payload_json']);kind=e['event_type']
            if kind=='outfit_upsert':
                docs.put('outfit_compositions',p,{'slot_id':p['slot_id']})
            elif kind=='outfit_delete':docs.delete('outfit_compositions',{'slot_id':p['slot_id']})
            elif kind=='notification':docs.put('notifications',{'id':e['id'],'created_at':e['created_at'],'read':False,**p},{'id':e['id']})
            elif kind=='activity':docs.put('activity_logs',{'id':e['id'],'created_at':e['created_at'],'note':'',**p},{'id':e['id']})
            elif kind=='borrow_log':docs.put('borrow_status_logs',{'id':e['id'],'created_at':e['created_at'],'note':'',**p},{'id':e['id']})
            with sql.tx() as t:t.execute('UPDATE outbox_events SET processed_at=? WHERE id=?',(STAMP(),e['id']))
        except Exception as exc:
            print('outbox retry later:',repr(exc))
            with sql.tx() as t:t.execute('UPDATE outbox_events SET attempts=attempts+1 WHERE id=?',(e['id'],))

async def outbox_worker():
    while True:
        await asyncio.sleep(6)
        try:await asyncio.to_thread(process_outbox)
        except Exception as exc:print('outbox-worker',repr(exc))

@contextlib.asynccontextmanager
async def lifespan(app):
    global docs
    # DB containers might take a moment to become ready
    for i in range(20):
        try:sql.setup();docs=Docs();break
        except Exception as exc:
            if i==19:raise
            await asyncio.sleep(2)
    seed_demo_if_empty()
    process_outbox()
    task=asyncio.create_task(outbox_worker())
    yield
    task.cancel()
    try:await task
    except asyncio.CancelledError:pass

app=FastAPI(title='WardrobeShare',lifespan=lifespan)
# Basic browser-CSRF boundary for an HTTPS deployment. Additional security review required.
PUBLIC_ORIGIN = os.getenv('PUBLIC_ORIGIN', '').strip().rstrip('/')
if IS_PROD and not PUBLIC_ORIGIN.startswith('https://'):
    raise RuntimeError('COOKIE_SECURE=1 requires PUBLIC_ORIGIN=https://your-real-domain')

@app.middleware('http')
async def verify_mutation_origin(req: Request, call_next):
    if IS_PROD and req.url.path.startswith('/api/') and req.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        from urllib.parse import urlsplit
        origin = (req.headers.get('origin') or '').rstrip('/')
        if not origin:
            referer = req.headers.get('referer') or ''
            parts = urlsplit(referer)
            if parts.scheme and parts.netloc:
                origin = parts.scheme + '://' + parts.netloc
        if origin != PUBLIC_ORIGIN:
            return JSONResponse({'error': 'Same-origin request required'}, status_code=403)
    if IS_PROD and req.method in ('POST', 'PUT', 'PATCH'):
        length = req.headers.get('content-length')
        if length and length.isdigit() and int(length) > 12 * 1024 * 1024:
            return JSONResponse({'error': 'Request body is too large'}, status_code=413)
    result = await call_next(req)
    if IS_PROD:
        result.headers['X-Content-Type-Options'] = 'nosniff'
        result.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        result.headers['X-Frame-Options'] = 'DENY'
        result.headers['Permissions-Policy'] = 'camera=(self), microphone=(), geolocation=()'
        result.headers['Cross-Origin-Resource-Policy'] = 'same-origin'
        result.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        result.headers['Content-Security-Policy'] = (
            "default-src 'self'; base-uri 'self'; form-action 'self'; object-src 'none'; "
            "frame-ancestors 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; connect-src 'self'; upgrade-insecure-requests"
        )
        if req.url.path.startswith(('/api/', '/media/')):
            result.headers['Cache-Control'] = 'private, no-store'
    return result
app.mount('/assets',StaticFiles(directory=str(ROOT/'static/assets')),name='assets')
app.mount('/css',StaticFiles(directory=str(ROOT/'static/css')),name='css')
app.mount('/js',StaticFiles(directory=str(ROOT/'static/js')),name='js')
@app.get('/media/{filename}')
def protected_media(filename:str,req:Request):
    user=current(req)
    require(bool(re.fullmatch(r'[0-9a-f]{32}(-original)?\.png',filename)),'Not found',404)
    url='/media/'+filename
    with sql.tx() as t:
        cloth=t.one('SELECT owner_id,original_path,image_path FROM clothing_items WHERE image_path=? OR original_path=?',(url,url))
        portrait=t.one('SELECT id FROM users WHERE avatar_path=?',(url,))
    # Avatar portraits are discoverable through the logged-in Friends search.
    require(portrait is not None or (cloth is not None and (cloth['owner_id']==user['id'] or (url==cloth['image_path'] and allowed_clothing(user['id'],cloth['owner_id'])))),'Not authorized to see this photo',403)
    require((DATE_MEDIA/filename).is_file(),'Not found',404)
    return FileResponse(DATE_MEDIA/filename)


@app.exception_handler(HTTPException)
async def errors(req,exc):return JSONResponse({'error':exc.detail},status_code=exc.status_code)

def fail(message,code=400):raise HTTPException(code,message)
def require(cond,message='Forbidden',code=403):
    if not cond:fail(message,code)
def hash_pw(pw):
    salt=secrets.token_hex(16)
    digest=hashlib.pbkdf2_hmac('sha256',pw.encode(),bytes.fromhex(salt),260000).hex()
    return salt+':'+digest
def check_pw(pw,stored):
    try:
        salt,digest=stored.split(':');got=hashlib.pbkdf2_hmac('sha256',pw.encode(),bytes.fromhex(salt),260000).hex()
        return hmac.compare_digest(got,digest)
    except Exception:return False

def session_cookie(r:Response,user_id):
    secret=secrets.token_urlsafe(32);token_hash=hashlib.sha256(secret.encode()).hexdigest()
    expiration=(datetime.now(timezone.utc)+timedelta(days=14)).isoformat()
    with sql.tx() as t:t.insert('sessions',{'id_hash':token_hash,'user_id':user_id,'expires_at':expiration})
    r.set_cookie(COOKIE_NAME,secret,httponly=True,secure=IS_PROD,samesite='lax',max_age=14*86400,path='/')
def current(req:Request):
    token=req.cookies.get(COOKIE_NAME)
    if not token:fail('Please sign in',401)
    with sql.tx() as t:
        u=t.one('SELECT u.id,u.username,u.email,u.display_name,u.avatar_path,u.bio,u.created_at FROM users u JOIN sessions s ON u.id=s.user_id WHERE s.id_hash=? AND s.expires_at>?',(hashlib.sha256(token.encode()).hexdigest(),STAMP()))
    if not u:fail('Your session has expired',401)
    return u

def user_public(row):
    if not row:return None
    return {k:row.get(k) for k in ('id','username','display_name','avatar_path','bio')}
def owner_cloth(row):
    with sql.tx() as t:
        owner=t.one('SELECT username,display_name FROM users WHERE id=?',(row['owner_id'],))
        holder=t.one('SELECT username,display_name FROM users WHERE id=?',(row['current_holder_id'],)) if row.get('current_holder_id') else None
    return {**row,'owner':owner['display_name'] if owner else 'Unknown','owner_username':owner['username'] if owner else '', 'holder':holder['display_name'] if holder else None}

def friends_of(user_id):
    with sql.tx() as t:
        rows=t.all("SELECT requester_id,addressee_id FROM friendships WHERE status='accepted' AND (requester_id=? OR addressee_id=?)",(user_id,user_id))
    return {r['addressee_id'] if r['requester_id']==user_id else r['requester_id'] for r in rows}

def allowed_clothing(viewer,owner):return viewer==owner or owner in friends_of(viewer)

def outfits_of(user_id,viewer_id):
    if not allowed_clothing(viewer_id,user_id):return []
    allowed=friends_of(viewer_id)|{viewer_id}
    with sql.tx() as t:slots=t.all('SELECT * FROM outfit_slots WHERE owner_id=? ORDER BY updated_at DESC',(user_id,))
    raw={d['slot_id']:d for d in docs.read('outfit_compositions',{'owner_id':user_id})}
    results=[]
    for s in slots:
        comp=raw.get(s['id'])
        if not comp:continue
        itemrefs=comp.get('items',[])
        # Author-owned OOTDs are visible to author, but pieces from no-longer-friends
        # are redacted. Another viewer gets NO CARD if any owner is not allowed.
        if viewer_id!=user_id and any(it.get('owner_id') not in allowed for it in itemrefs):continue
        resolved=[]
        with sql.tx() as t:
            for it in itemrefs:
                clothing=t.one('SELECT id,name,image_path,owner_id,deleted_at FROM clothing_items WHERE id=?',(it.get('item_id'),))
                if viewer_id==user_id and it.get('owner_id') not in allowed:
                    resolved.append({**it,'name':'Unavailable item','image_path':'','unavailable':True});continue
                if clothing and not clothing.get('deleted_at'):
                    owner=t.one('SELECT username,display_name FROM users WHERE id=?',(clothing['owner_id'],))
                    resolved.append({**it,'name':clothing['name'],'image_path':clothing['image_path'],'owner_name':owner['display_name'] if owner else 'Unknown','owner_username':owner['username'] if owner else '', 'unavailable':False})
                else:
                    resolved.append({**it,'name':'Deleted item','image_path':'','unavailable':True})
        results.append({**s,'items':resolved,'item_count':len(resolved)})
    return results

def profile_with_counts(u,viewer):
    with sql.tx() as t:n=t.one('SELECT COUNT(*) AS n FROM clothing_items WHERE owner_id=? AND deleted_at IS NULL',(u['id'],))['n']
    return {**user_public(u),'items_count':n,'ootds_count':len(outfits_of(u['id'],viewer))}

@app.post('/api/auth/register')
async def register(req:Request,response:Response):
    rate_limit('register-ip:'+source_key(req), 12, 3600)
    d=await req.json();name=str(d.get('username','')).lower().strip();email=str(d.get('email','')).lower().strip();pw=str(d.get('password',''))
    require(len(pw) <= 256, 'Password is too long')
    require(bool(re.fullmatch(r'[a-z0-9_.]{3,24}',name)),'Username must be 3–24 letters, numbers, underscore or period')
    require(bool(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email)),'Invalid email')
    require(len(pw)>=8,'Password must be at least 8 characters')
    with sql.tx() as t:
        if MAX_BETA_USERS:
            # Registration count is checked under the process lock. This initial
            # beta uses one Uvicorn worker; deploy multiple replicas only after
            # introducing a database-enforced user quota.
            count=t.one('SELECT COUNT(*) AS n FROM users WHERE password_hash != ?',('!deleted!',))['n']
            require(count < MAX_BETA_USERS,'This closed beta has reached its member limit',403)
        if t.one('SELECT id FROM users WHERE username=? OR email=?',(name,email)):fail('Username or email already taken',409)
        ident=UID();t.insert('users',{'id':ident,'username':name,'email':email,'password_hash':hash_pw(pw),'display_name':str(d.get('display_name') or name)[:60],'created_at':STAMP()})
    session_cookie(response,ident)
    return {'ok':True,'user_id':ident}

@app.post('/api/auth/login')
async def login(req:Request,response:Response):
    d=await req.json();q=str(d.get('username','')).lower().strip()[:254]
    rate_limit('login-ip:'+source_key(req), 90, 600)
    rate_limit('login-account:'+hashlib.sha256(q.encode()).hexdigest(), 12, 600)
    supplied_password=str(d.get('password',''))
    if len(supplied_password)>256: fail('Incorrect username or password',401)
    with sql.tx() as t:u=t.one('SELECT * FROM users WHERE username=? OR email=?',(q,q))
    if not u or u['password_hash']=='!deleted!' or not check_pw(supplied_password,u['password_hash']):fail('Incorrect username or password',401)
    session_cookie(response,u['id']);return {'ok':True}

@app.post('/api/auth/logout')
def logout(req:Request,response:Response):
    token=req.cookies.get(COOKIE_NAME)
    if token:
        with sql.tx() as t:t.execute('DELETE FROM sessions WHERE id_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))
    response.delete_cookie(COOKIE_NAME,path='/');return {'ok':True}

@app.get('/api/me')
def me(user=Depends(current)):return user

@app.patch('/api/me')
async def update_profile(req:Request,user=Depends(current)):
    d=await req.json();updates={}
    if 'display_name' in d:updates['display_name']=str(d['display_name']).strip()[:60]
    if 'bio' in d:updates['bio']=str(d['bio'])[:280]
    if 'email' in d:
        email=str(d['email']).lower().strip()
        require(bool(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email)),'Invalid email')
        with sql.tx() as t:
            if t.one('SELECT id FROM users WHERE email=? AND id<>?',(email,user['id'])):fail('Email already used',409)
        updates['email']=email
    if d.get('new_password'):
        new_pw=str(d['new_password']);old_pw=str(d.get('current_password',''))
        require(len(new_pw)>=8,'New password must be at least 8 characters')
        with sql.tx() as t: prior=t.one('SELECT password_hash FROM users WHERE id=?',(user['id'],))
        require(check_pw(old_pw,prior['password_hash']),'Current password is incorrect',403)
        updates['password_hash']=hash_pw(new_pw)
    if 'username' in d:
        name=str(d['username']).lower().strip()
        require(bool(re.fullmatch(r'[a-z0-9_.]{3,24}',name)),'Invalid username')
        with sql.tx() as t:
            if t.one('SELECT id FROM users WHERE username=? AND id<>?',(name,user['id'])):fail('Username already used',409)
        updates['username']=name
    if updates:
        with sql.tx() as t:t.execute('UPDATE users SET '+','.join(f'{k}=?' for k in updates)+' WHERE id=?',(*updates.values(),user['id']))
    return {'ok':True}

async def save_image(file:UploadFile,background=False):
    content=await file.read(8*1024*1024+1)
    require(0<len(content)<=8*1024*1024,'Image must be 8 MB or less',413)
    try:
        source=Image.open(io.BytesIO(content))
        require(source.format in ('JPEG','PNG','WEBP'),'Only JPEG, PNG, or WEBP images are accepted',415)
        require(source.width*source.height<=18_000_000,'Image dimensions too large',413)
        im=ImageOps.exif_transpose(source).convert('RGBA')
        im.thumbnail((1500,1500))
    except HTTPException:
        raise
    except Exception:
        fail('Upload a valid image file')
    name=UID();original=name+'-original.png'
    ai=False;processed=im
    if background:
        if MODEL_FILE.is_file():
            # Never download model weights at runtime; this file is deliberately
            # supplied locally by the operator before deployment.
            try:
                def in_process_cutout():
                    global _ai_session
                    from rembg import new_session, remove
                    with _ai_lock:
                        if _ai_session is None:
                            _ai_session = new_session('u2netp')
                        b=io.BytesIO(); im.save(b,'PNG')
                        return remove(b.getvalue(),session=_ai_session)
                result=await asyncio.to_thread(in_process_cutout)
                processed=Image.open(io.BytesIO(result)).convert('RGBA');ai=True
            except Exception as exc:
                if REQUIRE_BACKGROUND_AI:fail('Local background removal could not process the picture: '+type(exc).__name__,503)
                print('In-process background removal not available:',repr(exc))
        elif REQUIRE_BACKGROUND_AI:
            fail('Local AI model unavailable; install u2netp.onnx under U2NET_HOME before uploading.',503)
    im.save(DATE_MEDIA/original,'PNG',optimize=True)
    filename=name+'.png';processed.save(DATE_MEDIA/filename,'PNG',optimize=True)
    return '/media/'+filename,'/media/'+original,ai

@app.delete('/api/me')
def close_account(req:Request,response:Response,user=Depends(current)):
    """Delete account access and personal data while retaining relational
    loan evidence as pseudonymous records. Active lending prevents closure."""
    with sql.tx() as t:
        active=t.one("SELECT id FROM borrow_requests WHERE (owner_id=? OR borrower_id=?) AND status IN ('pending','approved','borrowed','return_requested') LIMIT 1",(user['id'],user['id']))
        require(active is None,'Resolve your active borrowing requests before closing your account',409)
        slots=t.all('SELECT id FROM outfit_slots WHERE owner_id=?',(user['id'],))
        for o in slots:event(t,'outfit_delete',slot_id=o['id'])
        t.execute('DELETE FROM outfit_slots WHERE owner_id=?',(user['id'],))
        t.execute('DELETE FROM friendships WHERE requester_id=? OR addressee_id=?',(user['id'],user['id']))
        t.execute('UPDATE clothing_items SET deleted_at=COALESCE(deleted_at,?) WHERE owner_id=?',(STAMP(),user['id']))
        t.execute('DELETE FROM sessions WHERE user_id=?',(user['id'],))
        t.execute('UPDATE users SET username=?,email=?,password_hash=?,display_name=?,bio=?,avatar_path=NULL WHERE id=?',
                  ('closed_'+user['id'][:16],user['id']+'@closed.invalid','!deleted!','Deleted account','',user['id']))
    # Pseudonymized historical activity remains for loan consistency;
    # user-authored document entries and notifications are deleted.
    process_outbox()
    docs.delete('notifications',{'user_id':user['id']})
    docs.delete('activity_logs',{'user_id':user['id']})
    docs.delete('borrow_status_logs',{'user_id':user['id']})
    response.delete_cookie(COOKIE_NAME,path='/')
    return {'ok':True}

@app.post('/api/me/avatar')
async def upload_avatar(req:Request,file:UploadFile=File(...),user=Depends(current)):
    rate_limit('avatar-user:'+user['id'], 12, 3600)
    path,_,_=await save_image(file)
    with sql.tx() as t:t.execute('UPDATE users SET avatar_path=? WHERE id=?',(path,user['id']))
    return {'avatar_path':path}

@app.get('/api/wardrobe')
def wardrobe(owner:str='me',user=Depends(current)):
    uid=user['id'] if owner=='me' else owner
    require(allowed_clothing(user['id'],uid),'You must be accepted friends to view this wardrobe')
    with sql.tx() as t:items=t.all('SELECT * FROM clothing_items WHERE owner_id=? AND deleted_at IS NULL ORDER BY created_at DESC',(uid,))
    return {'items':[owner_cloth(i) for i in items]}

@app.post('/api/wardrobe')
async def add_clothing(req:Request,name:str=Form(...),category:str=Form(...),color:str=Form(''),details:str=Form(''),file:UploadFile=File(...),request_token:str=Form(''),user=Depends(current)):
    rate_limit('clothing-upload:'+user['id'], 40, 3600)
    require(1<=len(name.strip())<=100,'Clothing name must be 1-100 characters')
    require(category in CATEGORIES,'Invalid category',400)
    require(len(request_token)<=100,'Invalid upload request token')
    if request_token:
        # A retry with the same form token must return the original clothing item.
        with sql.tx() as t:
            previous=t.one('SELECT clothing_id FROM clothing_upload_requests WHERE user_id=? AND request_token=?',(user['id'],request_token))
            if previous:
                item=t.one('SELECT * FROM clothing_items WHERE id=?',(previous['clothing_id'],))
                return {'id':previous['clothing_id'],'image_path':item['image_path'],'original_path':item['original_path'],'ai_processed':bool(item['ai_processed']),'duplicate':True}
    path,orig,processed=await save_image(file,True);ident=UID();duplicate=None
    try:
        with sql.tx() as t:
            # Insert the referenced clothing row first to satisfy immediate FK checks.
            t.insert('clothing_items',{'id':ident,'owner_id':user['id'],'name':name.strip(),'category':category[:40],'color':color[:40],'details':details[:400],'image_path':path,'original_path':orig,'ai_processed':int(processed),'created_at':STAMP()})
            if request_token:
                # Unique key also protects against duplicate concurrent uploads on different workers.
                inserted=t.execute('INSERT INTO clothing_upload_requests (user_id,request_token,clothing_id,created_at) VALUES (?,?,?,?) ON CONFLICT (user_id,request_token) DO NOTHING',(user['id'],request_token,ident,STAMP()))
                if inserted.rowcount==0:
                    previous=t.one('SELECT clothing_id FROM clothing_upload_requests WHERE user_id=? AND request_token=?',(user['id'],request_token))
                    duplicate=t.one('SELECT * FROM clothing_items WHERE id=?',(previous['clothing_id'],))
                    # This request lost the race. Roll back only its newly inserted row.
                    t.execute('DELETE FROM clothing_items WHERE id=?',(ident,))
            if duplicate is None:
                event(t,'activity',user_id=user['id'],action='add_clothing',message=f'Added {name.strip()}')
    except Exception:
        for saved_path in (path,orig):
            (DATE_MEDIA/Path(saved_path).name).unlink(missing_ok=True)
        raise
    if duplicate:
        for saved_path in (path,orig):
            (DATE_MEDIA/Path(saved_path).name).unlink(missing_ok=True)
        return {'id':duplicate['id'],'image_path':duplicate['image_path'],'original_path':duplicate['original_path'],'ai_processed':bool(duplicate['ai_processed']),'duplicate':True}
    process_outbox()
    return {'id':ident,'image_path':path,'original_path':orig,'ai_processed':processed,'duplicate':False}

@app.patch('/api/wardrobe/{ident}')
async def edit_clothing(ident:str,req:Request,user=Depends(current)):
    d=await req.json()
    if 'name' in d:
        d['name']=str(d['name']).strip()
        require(1<=len(d['name'])<=100,'Clothing name must be 1-100 characters',400)
    if 'category' in d:require(d['category'] in CATEGORIES,'Invalid category',400)
    with sql.tx() as t:
        old=t.one('SELECT * FROM clothing_items WHERE id=? AND deleted_at IS NULL',(ident,))
        require(old is not None,'Clothing not found',404);require(old['owner_id']==user['id'])
        fields={k:str(d[k])[:400 if k=='details' else 100] for k in ('name','category','color','details') if k in d}
        if fields:t.execute('UPDATE clothing_items SET '+','.join(f'{k}=?' for k in fields)+' WHERE id=?',(*fields.values(),ident))
        event(t,'activity',user_id=user['id'],action='edit_clothing',message=f'Updated {old["name"]}')
    process_outbox();return {'ok':True}

@app.delete('/api/wardrobe/{ident}')
def delete_clothing(ident:str,user=Depends(current)):
    with sql.tx() as t:
        old=t.one('SELECT * FROM clothing_items WHERE id=? AND deleted_at IS NULL',(ident,))
        require(old is not None,'Clothing not found',404);require(old['owner_id']==user['id'])
        require(old['state']=='available','Cannot remove a reserved or borrowed item')
        t.execute('UPDATE clothing_items SET deleted_at=? WHERE id=?',(STAMP(),ident))
        event(t,'activity',user_id=user['id'],action='delete_clothing',message=f'Removed {old["name"]}')
    process_outbox();return {'ok':True}

@app.get('/api/users/search')
def search_users(q:str='',user=Depends(current)):
    q=q.strip().lower()
    if not q:return {'users':[]}
    with sql.tx() as t:
        matches=t.all('SELECT id,username,display_name,avatar_path,bio FROM users WHERE username LIKE ? AND id<>? AND password_hash!=? LIMIT 20',('%'+q+'%',user['id'],'!deleted!'))
    return {'users':[user_public(u) for u in matches]}

def friend_record(a,b,t):
    return t.one('SELECT * FROM friendships WHERE (requester_id=? AND addressee_id=?) OR (requester_id=? AND addressee_id=?)',(a,b,b,a))

@app.get('/api/friends')
def friends(user=Depends(current)):
    with sql.tx() as t:
        rows=t.all('SELECT * FROM friendships WHERE requester_id=? OR addressee_id=? ORDER BY updated_at DESC',(user['id'],user['id']))
        output={'friends':[],'incoming':[],'outgoing':[]}
        for r in rows:
            other_id=r['addressee_id'] if r['requester_id']==user['id'] else r['requester_id']
            other=t.one('SELECT * FROM users WHERE id=?',(other_id,))
            if not other:continue
            obj={**user_public(other),'friendship_id':r['id'],'created_at':r['created_at']}
            if r['status']=='accepted':output['friends'].append(obj)
            elif r['status']=='pending':output['outgoing' if r['requester_id']==user['id'] else 'incoming'].append(obj)
    return output

@app.post('/api/friends')
async def add_friend(req:Request,user=Depends(current)):
    username=str((await req.json()).get('username','')).lower().strip()
    with sql.tx() as t:
        other=t.one('SELECT * FROM users WHERE username=?',(username,))
        require(other is not None,'Username not found',404);require(other['id']!=user['id'],'Cannot friend yourself')
        existing=friend_record(user['id'],other['id'],t)
        if existing and existing['status'] in ('accepted','pending'):fail('Friendship/request already exists',409)
        if existing:t.execute('DELETE FROM friendships WHERE id=?',(existing['id'],))
        t.insert('friendships',{'id':UID(),'requester_id':user['id'],'addressee_id':other['id'],'status':'pending','created_at':STAMP(),'updated_at':STAMP()})
        event(t,'notification',user_id=other['id'],title='New friend request',message=f'{user["display_name"]} wants to be friends')
    process_outbox();return {'ok':True}

@app.patch('/api/friends/{ident}')
async def friend_decision(ident:str,req:Request,user=Depends(current)):
    d=await req.json();action=d.get('action')
    with sql.tx() as t:
        r=t.one('SELECT * FROM friendships WHERE id=?',(ident,));require(r is not None,'Request not found',404)
        if action=='accept':
            require(r['addressee_id']==user['id'] and r['status']=='pending')
            t.execute("UPDATE friendships SET status='accepted',updated_at=? WHERE id=?",(STAMP(),ident))
            event(t,'notification',user_id=r['requester_id'],title='Friend request accepted',message=f'{user["display_name"]} is now your friend')
        elif action in ('reject','cancel'):
            require(r['status']=='pending');require((action=='reject' and r['addressee_id']==user['id']) or (action=='cancel' and r['requester_id']==user['id']))
            t.execute('DELETE FROM friendships WHERE id=?',(ident,))
        else:fail('Invalid action')
    process_outbox();return {'ok':True}

@app.delete('/api/friends/{ident}')
def remove_friend(ident:str,user=Depends(current)):
    with sql.tx() as t:
        r=t.one('SELECT * FROM friendships WHERE id=?',(ident,))
        require(r is not None and r['status']=='accepted' and user['id'] in (r['requester_id'],r['addressee_id']),'Friendship not found',404)
        t.execute('DELETE FROM friendships WHERE id=?',(ident,))
        # Requests not yet handed over end with the friendship. Borrowed items
        # keep their request so the return flow can still finish.
        a,b=r['requester_id'],r['addressee_id']
        stale=t.all("SELECT * FROM borrow_requests WHERE ((owner_id=? AND borrower_id=?) OR (owner_id=? AND borrower_id=?)) AND status IN ('pending','approved')",(a,b,b,a))
        for s in stale:
            item=t.one('SELECT name FROM clothing_items WHERE id=?',(s['item_id'],))
            if s['status']=='approved':t.execute("UPDATE clothing_items SET state='available' WHERE id=? AND state='reserved'",(s['item_id'],))
            t.execute("UPDATE borrow_requests SET status='cancelled',updated_at=? WHERE id=?",(STAMP(),s['id']))
            for party in (s['owner_id'],s['borrower_id']):
                event(t,'borrow_log',user_id=party,request_id=s['id'],item_id=s['item_id'],action='cancelled',message=f'{item["name"]}: cancelled after friendship ended')
            other=s['owner_id'] if s['borrower_id']==user['id'] else s['borrower_id']
            event(t,'notification',user_id=other,title='Borrow request cancelled',message=f'The request for {item["name"]} was cancelled because the friendship ended')
    process_outbox();return {'ok':True}

@app.get('/api/friends/{ident}/closet')
def friend_closet(ident:str,user=Depends(current)):
    require(ident!=user['id'] and ident in friends_of(user['id']),'Only accepted friends can view this closet')
    with sql.tx() as t:
        other=t.one('SELECT * FROM users WHERE id=?',(ident,))
        items=t.all('SELECT * FROM clothing_items WHERE owner_id=? AND deleted_at IS NULL ORDER BY created_at DESC',(ident,))
    require(other is not None,'User not found',404)
    return {'user':profile_with_counts(other,user['id']),'items':[owner_cloth(i) for i in items],'ootds':outfits_of(ident,user['id'])}

@app.get('/api/ootds')
def list_ootds(user=Depends(current)):
    process_outbox();return {'ootds':outfits_of(user['id'],user['id'])}

async def validate_outfit_items(itemrefs,viewer):
    require(isinstance(itemrefs,list) and len(itemrefs)<=30,'Invalid OOTD items (maximum 30)')
    verified=[]
    with sql.tx() as t:
        for obj in itemrefs:
            require(isinstance(obj,dict),'Invalid item')
            item=t.one('SELECT * FROM clothing_items WHERE id=? AND deleted_at IS NULL',(obj.get('item_id'),))
            require(item is not None,'One of these clothing items no longer exists',404)
            require(allowed_clothing(viewer,item['owner_id']),'Cannot use clothes from someone who is not an accepted friend')
            try:x,y,w,z=float(obj.get('x',0)),float(obj.get('y',0)),float(obj.get('w',.3)),int(obj.get('z',0))
            except Exception:fail('Invalid item positions')
            require(-.5<=x<=1.5 and -.5<=y<=1.5 and .06<=w<=1.5 and -100<=z<=1000,'Invalid item dimensions')
            verified.append({'item_id':item['id'],'owner_id':item['owner_id'],'x':x,'y':y,'w':w,'z':z})
    return verified

@app.post('/api/ootds')
async def create_ootd(req:Request,user=Depends(current)):
    d=await req.json();title=str(d.get('title','')).strip()[:80]
    require(bool(title),'Please name your OOTD')
    items=await validate_outfit_items(d.get('items',[]),user['id']);require(bool(items),'Add at least one item')
    ident=UID();now=STAMP()
    with sql.tx() as t:
        t.insert('outfit_slots',{'id':ident,'owner_id':user['id'],'title':title,'created_at':now,'updated_at':now})
        event(t,'outfit_upsert',slot_id=ident,owner_id=user['id'],items=items)
        event(t,'activity',user_id=user['id'],action='create_ootd',message=f'Created OOTD {title}')
    process_outbox();return {'id':ident}

@app.patch('/api/ootds/{ident}')
async def edit_ootd(ident:str,req:Request,user=Depends(current)):
    d=await req.json();title=str(d.get('title','')).strip()[:80]
    require(bool(title),'Please name your OOTD')
    items=await validate_outfit_items(d.get('items',[]),user['id']);require(bool(items),'Add at least one item')
    with sql.tx() as t:
        slot=t.one('SELECT * FROM outfit_slots WHERE id=?',(ident,));require(slot is not None,'OOTD not found',404);require(slot['owner_id']==user['id'])
        t.execute('UPDATE outfit_slots SET title=?,updated_at=? WHERE id=?',(title,STAMP(),ident))
        event(t,'outfit_upsert',slot_id=ident,owner_id=user['id'],items=items)
    process_outbox();return {'ok':True}

@app.delete('/api/ootds/{ident}')
def delete_ootd(ident:str,user=Depends(current)):
    with sql.tx() as t:
        slot=t.one('SELECT * FROM outfit_slots WHERE id=?',(ident,));require(slot is not None,'OOTD not found',404);require(slot['owner_id']==user['id'])
        t.execute('DELETE FROM outfit_slots WHERE id=?',(ident,))
        event(t,'outfit_delete',slot_id=ident)
    process_outbox();return {'ok':True}

@app.get('/api/borrowing')
def borrowing(user=Depends(current)):
    with sql.tx() as t:
        reqs=t.all('SELECT * FROM borrow_requests WHERE owner_id=? OR borrower_id=? ORDER BY created_at DESC',(user['id'],user['id']))
        output=[]
        for r in reqs:
            item=t.one('SELECT * FROM clothing_items WHERE id=?',(r['item_id'],))
            owner=t.one('SELECT id,username,display_name,avatar_path FROM users WHERE id=?',(r['owner_id'],))
            borrower=t.one('SELECT id,username,display_name,avatar_path FROM users WHERE id=?',(r['borrower_id'],))
            output.append({**r,'item_name':item['name'] if item else '(removed)','image_path':item['image_path'] if item else '', 'owner':user_public(owner),'borrower':user_public(borrower),'is_owner':r['owner_id']==user['id']})
    return {'requests':output}

@app.post('/api/borrowing')
async def request_borrow(req:Request,user=Depends(current)):
    ident=(await req.json()).get('item_id')
    with sql.tx() as t:
        item=t.one('SELECT * FROM clothing_items WHERE id=? AND deleted_at IS NULL',(ident,))
        require(item is not None,'Item not found',404)
        require(item['owner_id']!=user['id'],'Cannot borrow your own item')
        require(item['owner_id'] in friends_of(user['id']),'Borrow only from accepted friends')
        require(item['state']=='available','This item is not available')
        existing=t.one("SELECT id FROM borrow_requests WHERE item_id=? AND borrower_id=? AND status IN ('pending','approved','borrowed','return_requested')",(ident,user['id']))
        require(existing is None,'You already have an active request for this item')
        rid=UID();t.insert('borrow_requests',{'id':rid,'item_id':ident,'owner_id':item['owner_id'],'borrower_id':user['id'],'status':'pending','created_at':STAMP(),'updated_at':STAMP()})
        event(t,'notification',user_id=item['owner_id'],title='Borrow request',message=f'{user["display_name"]} wants to borrow {item["name"]}')
        event(t,'borrow_log',user_id=user['id'],request_id=rid,item_id=ident,action='pending',message=f'Requested {item["name"]}')
    process_outbox();return {'id':rid}

@app.delete('/api/borrowing/{ident}')
def delete_terminal_borrow_request(ident:str,user=Depends(current)):
    """Remove a concluded request and associated SQL loan rows in one ACID
    transaction. Actual system audit events are retained separately in NoSQL."""
    with sql.tx() as t:
        r=t.one('SELECT * FROM borrow_requests WHERE id=?',(ident,))
        require(r is not None,'Request not found',404)
        require(user['id'] in (r['owner_id'],r['borrower_id']),'Only participants can delete history',403)
        require(r['status'] in ('cancelled','rejected','returned'),'Only completed/cancelled requests can be deleted',409)
        t.execute('DELETE FROM loans WHERE request_id=?',(ident,))
        t.execute('DELETE FROM borrow_requests WHERE id=?',(ident,))
        event(t,'activity',user_id=user['id'],action='delete_borrow_history',message='Removed a concluded borrowing entry')
    process_outbox()
    return {'ok':True}

@app.patch('/api/borrowing/{ident}')
async def borrow_action(ident:str,req:Request,user=Depends(current)):
    action=(await req.json()).get('action')
    with sql.tx() as t:
        q='SELECT * FROM borrow_requests WHERE id=?'+(' FOR UPDATE' if sql.pg else '')
        r=t.one(q,(ident,));require(r is not None,'Request not found',404)
        q='SELECT * FROM clothing_items WHERE id=?'+(' FOR UPDATE' if sql.pg else '')
        item=t.one(q,(r['item_id'],));require(item is not None,'Item not found',404)
        state=r['status'];owner=r['owner_id']==user['id'];borrower=r['borrower_id']==user['id']
        nextstate=None
        if action=='approve':
            require(owner and state=='pending' and item['state']=='available','Item not available or request no longer pending',409)
            nextstate='approved';t.execute("UPDATE clothing_items SET state='reserved' WHERE id=?",(item['id'],))
            competing=t.all("SELECT id,borrower_id FROM borrow_requests WHERE item_id=? AND status='pending' AND id<>?",(item['id'],ident))
            for other in competing:
                t.execute("UPDATE borrow_requests SET status='rejected',updated_at=? WHERE id=?",(STAMP(),other['id']))
                event(t,'notification',user_id=other['borrower_id'],title='Borrow request unavailable',message=f'{item["name"]} has been reserved for another borrower')
                event(t,'borrow_log',user_id=other['borrower_id'],request_id=other['id'],item_id=item['id'],action='rejected',message=f'{item["name"]}: request closed after another reservation')
            event(t,'notification',user_id=r['borrower_id'],title='Borrow request approved',message=f'{item["name"]} is ready for handover')
        elif action=='reject':
            require(owner and state=='pending');nextstate='rejected'
            event(t,'notification',user_id=r['borrower_id'],title='Borrow request declined',message=f'{user["display_name"]} declined your request for {item["name"]}')
        elif action=='cancel':
            require(borrower and state in ('pending','approved'));nextstate='cancelled'
            event(t,'notification',user_id=r['owner_id'],title='Borrow request cancelled',message=f'{user["display_name"]} cancelled the request for {item["name"]}')
        elif action=='handover':
            require(owner and state=='approved','Handover requires owner confirmation',409)
            require(item['state']=='reserved','Item is no longer reserved',409)
            nextstate='borrowed';t.execute("UPDATE clothing_items SET state='borrowed',current_holder_id=? WHERE id=?",(r['borrower_id'],item['id']))
            t.insert('loans',{'id':UID(),'request_id':r['id'],'item_id':item['id'],'lender_id':r['owner_id'],'borrower_id':r['borrower_id'],'status':'active','started_at':STAMP()})
            event(t,'notification',user_id=r['borrower_id'],title='Item handed over',message=f'{item["name"]} is now with you')
        elif action=='request_return':
            require(borrower and state=='borrowed');nextstate='return_requested'
            event(t,'notification',user_id=r['owner_id'],title='Return requested',message=f'{user["display_name"]} is returning {item["name"]}. Confirm once you have it back')
        elif action=='confirm_return':
            require(owner and state=='return_requested');nextstate='returned'
            t.execute("UPDATE clothing_items SET state='available',current_holder_id=NULL WHERE id=?",(item['id'],))
            t.execute("UPDATE loans SET status='returned',ended_at=? WHERE request_id=?",(STAMP(),r['id']))
            event(t,'notification',user_id=r['borrower_id'],title='Return confirmed',message=f'{item["name"]} has been returned')
        else:fail('Invalid borrowing action')
        if action=='cancel' and state=='approved':t.execute("UPDATE clothing_items SET state='available' WHERE id=?",(item['id'],))
        t.execute('UPDATE borrow_requests SET status=?,updated_at=? WHERE id=?',(nextstate,STAMP(),ident))
        event(t,'borrow_log',user_id=r['owner_id'],request_id=ident,item_id=item['id'],action=nextstate,message=f'{item["name"]}: {nextstate}')
        event(t,'borrow_log',user_id=r['borrower_id'],request_id=ident,item_id=item['id'],action=nextstate,message=f'{item["name"]}: {nextstate}')
        event(t,'activity',user_id=user['id'],action='borrow_'+action,message=f'{item["name"]} → {nextstate}')
    process_outbox();return {'ok':True,'status':nextstate}

@app.post('/api/notifications')
async def create_personal_notification(req:Request,user=Depends(current)):
    d=await req.json(); title=str(d.get('title','')).strip()[:90]; message=str(d.get('message','')).strip()[:300]
    require(bool(title) and bool(message),'Title and message are required')
    ident=UID();docs.put('notifications',{'id':ident,'user_id':user['id'],'title':title,'message':message,'read':False,'created_at':STAMP(),'kind':'personal_reminder'})
    return {'id':ident}

@app.get('/api/notifications')
def notifications(user=Depends(current)):
    process_outbox()
    items=docs.read('notifications',{'user_id':user['id']})
    return {'notifications':sorted(items,key=lambda x:x.get('created_at',''),reverse=True)}

@app.patch('/api/notifications/{ident}')
async def edit_notification(ident:str,req:Request,user=Depends(current)):
    d=await req.json()
    require(d.get('read') in (True,False),'Only read status may be changed')
    require(docs.patch('notifications',{'id':ident,'user_id':user['id']},{'read':d['read']})>0,'Notification not found',404)
    return {'ok':True}

@app.delete('/api/notifications/{ident}')
def delete_notification(ident:str,user=Depends(current)):
    require(docs.delete('notifications',{'id':ident,'user_id':user['id']})>0,'Notification not found',404)
    return {'ok':True}

@app.get('/api/activity')
def activity(user=Depends(current)):
    process_outbox()
    items=docs.read('activity_logs',{'user_id':user['id']});logs=docs.read('borrow_status_logs',{'user_id':user['id']})
    return {'activity':sorted(items,key=lambda x:x.get('created_at',''),reverse=True),'borrow_logs':sorted(logs,key=lambda x:x.get('created_at',''),reverse=True)}

@app.post('/api/activity')
async def add_activity_note(req:Request,user=Depends(current)):
    note=str((await req.json()).get('note','')).strip()[:300];require(bool(note),'Note cannot be empty')
    ident=UID();docs.put('activity_logs',{'id':ident,'user_id':user['id'],'action':'journal','message':note,'note':note,'created_at':STAMP()})
    return {'id':ident}

@app.patch('/api/activity/{ident}')
async def update_activity_note(ident:str,req:Request,user=Depends(current)):
    note=str((await req.json()).get('note','')).strip()[:300]
    require(bool(note),'Note cannot be empty')
    # immutable audit actions cannot be edited, only user-created journal entries
    found=[d for d in docs.read('activity_logs',{'id':ident,'user_id':user['id']}) if d.get('action')=='journal']
    require(bool(found),'Only your journal notes can be edited',403)
    docs.patch('activity_logs',{'id':ident,'user_id':user['id']},{'note':note,'message':note});return {'ok':True}

@app.delete('/api/activity/{ident}')
def delete_activity_note(ident:str,user=Depends(current)):
    found=[d for d in docs.read('activity_logs',{'id':ident,'user_id':user['id']}) if d.get('action')=='journal']
    require(bool(found),'Only your journal notes can be deleted',403)
    docs.delete('activity_logs',{'id':ident,'user_id':user['id']});return {'ok':True}

@app.post('/api/borrow-logs')
async def add_borrow_personal_note(req:Request,user=Depends(current)):
    d=await req.json(); note=str(d.get('note','')).strip()[:300]
    require(bool(note),'Note cannot be empty')
    ident=UID();docs.put('borrow_status_logs',{'id':ident,'user_id':user['id'],'action':'personal_note','message':note,'note':note,'created_at':STAMP(),'request_id':None,'item_id':None})
    return {'id':ident}

@app.delete('/api/borrow-logs/{ident}')
def delete_borrow_personal_note(ident:str,user=Depends(current)):
    existing=docs.read('borrow_status_logs',{'id':ident,'user_id':user['id']})
    require(bool(existing),'Log not found',404)
    require(existing[0].get('action')=='personal_note','System audit entries cannot be deleted',403)
    docs.delete('borrow_status_logs',{'id':ident,'user_id':user['id']})
    return {'ok':True}

@app.patch('/api/borrow-logs/{ident}')
async def annotate_borrow_log(ident:str,req:Request,user=Depends(current)):
    note=str((await req.json()).get('note',''))[:300]
    old=docs.read('borrow_status_logs',{'id':ident,'user_id':user['id']})
    require(bool(old),'Log not found',404)
    fields={'note':note}
    if old[0].get('action')=='personal_note':
        require(bool(note.strip()),'Note cannot be empty')
        fields['message']=note
    require(docs.patch('borrow_status_logs',{'id':ident,'user_id':user['id']},fields)>0,'Log not found',404)
    return {'ok':True}

@app.get('/api/health')
def health():return {'ok':True,'sql':'PostgreSQL' if sql.pg else 'SQLite preview','nosql':'MongoDB' if MONGO_URL else 'JSON preview','background_ai_enabled':MODEL_FILE.is_file(),'demo_enabled':os.getenv('SEED_DEMO','1')=='1' and not IS_PROD, 'invite_required':False}

@app.get('/api/ready')
def readiness():
    """Live dependency checks; unlike /api/health, failures return HTTP 503."""
    checks = {'sql': 'PostgreSQL' if sql.pg else 'SQLite preview',
              'nosql': 'MongoDB' if MONGO_URL else 'JSON preview',
              'background_ai': 'in-process model ready' if MODEL_FILE.is_file() else 'optional model not installed'}
    problems = []
    try:
        with sql.tx() as t: t.one('SELECT 1 AS ready')
    except Exception as e:
        problems.append('SQL unavailable: ' + type(e).__name__)
    try:
        if docs is None:
            raise RuntimeError('Documents not initialized')
        if docs.real:
            docs.client.admin.command('ping')
        else:
            docs.read('notifications')
    except Exception as e:
        problems.append('NoSQL unavailable: ' + type(e).__name__)
    if REQUIRE_BACKGROUND_AI:
        if not MODEL_FILE.is_file():
            problems.append('Required local u2netp.onnx model missing')
        else:
            try:
                # Validate the ONNX runtime and load the model once, not merely
                # the existence of the file. Uploads reuse this cached session.
                global _ai_session
                with _ai_lock:
                    if _ai_session is None:
                        from rembg import new_session
                        _ai_session = new_session('u2netp')
            except Exception as exc:
                problems.append('Local AI could not load: ' + type(exc).__name__)
    status = 503 if problems else 200
    return JSONResponse({'ready': not problems, **checks, 'problems': problems}, status_code=status)


@app.get('/{full_path:path}')
def index(full_path:str):
    if full_path.startswith(('api/','media/','assets/','css/','js/')):fail('Not found',404)
    return FileResponse(ROOT/'static/index.html')

# Seed lightweight, real locally stored clothing cutouts extracted from the user's
# own approved mockup screenshots. Only created on an empty database.
def seed_demo_if_empty():
    if os.getenv('SEED_DEMO','1')!='1':return
    with sql.tx() as t:
        if t.one('SELECT id FROM users LIMIT 1'):return
        users=[]
        for username,display in [('taa','Taa'),('may','May'),('june','June'),('pim','Pim'),('leo','Leo')]:
            ident=UID();users.append(ident)
            t.insert('users',{'id':ident,'username':username,'email':username+'@example.local','password_hash':hash_pw('demo12345'),'display_name':display,'bio':'little closet, big outfit dreams ♡','created_at':STAMP()})
        clothing=[('Cable Knit Cardigan','Tops','Cream','cream-cardigan.png'),('Gingham Blouse','Tops','Pink','gingham-blouse.png'),('Tiered Skirt','Bottoms','Brown','brown-skirt.png'),('Wide Leg Jeans','Bottoms','Blue','wide-jeans.png'),('Mary Jane Shoes','Shoes','Black','mary-janes.png'),('Metallic Shoulder Bag','Bags','Silver','silver-bag.png'),('Lace Mini Dress','Dresses','Cream','lace-dress.png'),('Plaid Scarf','Accessories','Brown','plaid-scarf.png')]
        owned=[]
        for uidx,uid in enumerate(users[:4]):
            group=[]
            for j,(name,cat,color,img) in enumerate(clothing):
                ci=UID();group.append(ci)
                t.insert('clothing_items',{'id':ci,'owner_id':uid,'name':name,'category':cat,'color':color,'details':'A favorite piece from my closet','image_path':'/assets/'+img,'original_path':'/assets/'+img,'ai_processed':1,'created_at':STAMP()})
            owned.append(group)
        for idx in (1,2,3):t.insert('friendships',{'id':UID(),'requester_id':users[0],'addressee_id':users[idx],'status':'accepted','created_at':STAMP(),'updated_at':STAMP()})
        # Taa's gallery: personal + mixed-with-friend pieces
        for title,indices in [('Café Day',[(0,0), (0,2),(0,4),(1,5)]),('Pink Mood',[(1,1),(0,6),(0,5)]),('Class Fit',[(0,1),(0,3),(0,4)])]:
            slot=UID();t.insert('outfit_slots',{'id':slot,'owner_id':users[0],'title':title,'created_at':STAMP(),'updated_at':STAMP()})
            positions=[(.08,.08,.46,1),(.37,.38,.49,2),(.04,.70,.26,4),(.65,.32,.26,3)]
            refs=[{'item_id':owned[user_idx][item_idx],'owner_id':users[user_idx],'x':positions[k][0],'y':positions[k][1],'w':positions[k][2],'z':positions[k][3]} for k,(user_idx,item_idx) in enumerate(indices)]
            event(t,'outfit_upsert',slot_id=slot,owner_id=users[0],items=refs)
        # May's outfits: one shareable, one containing Leo's item (hidden from Taa).
        for title,indices in [('Library Day',[(1,0),(1,2),(1,4)]),('Secret Picnic',[(1,1),(4,0)])]:
            slot=UID();t.insert('outfit_slots',{'id':slot,'owner_id':users[1],'title':title,'created_at':STAMP(),'updated_at':STAMP()})
            positions=[(.14,.08,.52,1),(.32,.46,.46,2),(.06,.70,.32,3)]
            refs=[]
            for k,(user_idx,item_idx) in enumerate(indices):
                # Put a real Leo-owned item into SQL for the privacy example below
                if user_idx==4:
                    ext=UID();t.insert('clothing_items',{'id':ext,'owner_id':users[4],'name':'Private Cardigan','category':'Tops','color':'Cream','details':'','image_path':'/assets/cream-cardigan.png','created_at':STAMP()})
                    itemid=ext
                else:itemid=owned[user_idx][item_idx]
                refs.append({'item_id':itemid,'owner_id':users[user_idx],'x':positions[k][0],'y':positions[k][1],'w':positions[k][2],'z':positions[k][3]})
            event(t,'outfit_upsert',slot_id=slot,owner_id=users[1],items=refs)
        event(t,'notification',user_id=users[0],title='Welcome to freesize',message='Your little closet is ready. Create your first OOTD!')
