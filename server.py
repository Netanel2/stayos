#!/usr/bin/env python3
"""STAYOS v3 multi-hotel pilot platform. SQLite/one instance MVP; review before production."""
import os, sys, json, re, sqlite3, secrets, hashlib, hmac, time, io, mimetypes, threading, contextvars
from pathlib import Path
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qs, quote
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from contextlib import contextmanager
from PIL import Image, UnidentifiedImageError, ImageOps
import qrcode

ROOT=Path(__file__).resolve().parent
DATA=Path(os.environ.get('STAYOS_DATA_DIR', '/data' if os.environ.get('RAILWAY_ENVIRONMENT') else str(ROOT/'local-data')))
DB=Path(os.environ.get('STAYOS_PLATFORM_DB', str(DATA/'platform.sqlite')))
MEDIA=Path(os.environ.get('STAYOS_MEDIA_DIR', str(DATA/'media')))
PORT=int(os.environ.get('PORT','8765'))
SECURE=os.environ.get('STAYOS_COOKIE_SECURE','1' if os.environ.get('RAILWAY_ENVIRONMENT') else '0')=='1'
DEMO_MODE=os.environ.get('STAYOS_DEMO_SEED','1')=='1'
OWNER_PASSWORD=os.environ.get('STAYOS_ADMIN_PASSWORD','')
IS_CLOUD=bool(os.environ.get('RAILWAY_ENVIRONMENT'))
if IS_CLOUD and (not OWNER_PASSWORD or OWNER_PASSWORD=='stayos-demo' or len(OWNER_PASSWORD)<12):
    raise RuntimeError('Set STAYOS_ADMIN_PASSWORD in Railway Variables to a unique strong password (12+ chars)')
if not OWNER_PASSWORD: OWNER_PASSWORD='local-demo-only-change-me'
ALL_PERMS=['guests','requests','orders','folio','chat','rooms','menu','activities','amenities','services','branding','media']
VALID_PERMS=set(ALL_PERMS+[mode+':'+p for p in ALL_PERMS for mode in ('view','edit')])
FIELDS={
 'room_types':{'name_en':'str','name_he':'str','description_en':'text','description_he':'text','size':'str','max_guests':'int','image':'image','active':'bool'},
 'rooms':{'room':'str','room_type_id':'int','floor':'str','notes':'text','image':'image','active':'bool'},
 'menu':{'name_en':'str','name_he':'str','description_en':'text','description_he':'text','category':'str','price_cents':'money','image':'image','active':'bool'},
 'activities':{'name_en':'str','name_he':'str','description_en':'text','description_he':'text','starts':'date','ends':'date','location':'str','capacity':'int','price_cents':'money','image':'image','active':'bool'},
 'amenities':{'name_en':'str','name_he':'str','basis':'str','period':'str','quota':'int','image':'image','active':'bool'},
 'services':{'name_en':'str','name_he':'str','description_en':'text','description_he':'text','category':'str','image':'image','active':'bool'},
}
RESOURCE_PERM={'room_types':'rooms','rooms':'rooms','menu':'menu','activities':'activities','amenities':'amenities','services':'services'}
DEF_IMG={'room_types':'room-garden.webp','menu':'coconut.webp','activities':'yoga.webp','amenities':'resort-pool.webp','services':'wellness-spa.webp'}

CURRENT_CONN=contextvars.ContextVar("stayos_active_connection",default=None)

@contextmanager
def conn():
    # Each request uses its own connection, avoiding cross-hotel request context.
    c=sqlite3.connect(DB,timeout=20)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA busy_timeout=20000')
    c.execute('PRAGMA foreign_keys=ON')
    token=CURRENT_CONN.set(c)
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback();raise
    finally:
        CURRENT_CONN.reset(token)
        c.close()

def utcnow():return datetime.now(timezone.utc).isoformat()
def parse_dt(s):
    try:
        d=datetime.fromisoformat(str(s).replace('Z','+00:00'))
        if d.tzinfo is None: raise ValueError()
        return d
    except Exception: raise Bad('Invalid ISO timestamp',400)
def jsonobj(row):return dict(row) if row else None
def hash_pass(password,salt=None):
    salt=salt or secrets.token_bytes(16)
    return salt.hex()+'$'+hashlib.pbkdf2_hmac('sha256',password.encode(),salt,280000).hex()
def verify_pass(password,stored):
    try:
        a,b=stored.split('$')
        return hmac.compare_digest(hash_pass(password,bytes.fromhex(a)),stored)
    except Exception:return False

def audit(c,hotel_id,user_id,action,entity,entity_id,details=''):
    c.execute('INSERT INTO audit(hotel_id,user_id,action,entity,entity_id,details,created) VALUES(?,?,?,?,?,?,?)',(hotel_id,user_id,action,entity,str(entity_id),str(details)[:400],utcnow()))

def init_db():
    DB.parent.mkdir(parents=True,exist_ok=True);MEDIA.mkdir(parents=True,exist_ok=True)
    with conn() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS hotels(id INTEGER PRIMARY KEY,slug TEXT UNIQUE NOT NULL,name TEXT NOT NULL,location TEXT NOT NULL DEFAULT '',currency TEXT NOT NULL DEFAULT 'USD',language TEXT NOT NULL DEFAULT 'en',welcome_en TEXT NOT NULL DEFAULT '',welcome_he TEXT NOT NULL DEFAULT '',description_en TEXT NOT NULL DEFAULT '',description_he TEXT NOT NULL DEFAULT '',primary_color TEXT NOT NULL DEFAULT '#123b36',accent_color TEXT NOT NULL DEFAULT '#c3a473',hero_image TEXT NOT NULL DEFAULT 'resort-hero.webp',logo_image TEXT NOT NULL DEFAULT '',demo INTEGER NOT NULL DEFAULT 0,active INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,hotel_id INTEGER REFERENCES hotels(id),username TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,display_name TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN('owner','manager','staff')),permissions TEXT NOT NULL DEFAULT '[]',active INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),expires TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS room_types(id INTEGER PRIMARY KEY,hotel_id INTEGER NOT NULL REFERENCES hotels(id),name_en TEXT NOT NULL DEFAULT '',name_he TEXT NOT NULL DEFAULT '',description_en TEXT NOT NULL DEFAULT '',description_he TEXT NOT NULL DEFAULT '',size TEXT NOT NULL DEFAULT '',max_guests INTEGER NOT NULL DEFAULT 2,image TEXT NOT NULL DEFAULT '',active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS rooms(id INTEGER PRIMARY KEY,hotel_id INTEGER NOT NULL REFERENCES hotels(id),room TEXT NOT NULL,room_type_id INTEGER NOT NULL REFERENCES room_types(id),floor TEXT NOT NULL DEFAULT '',notes TEXT NOT NULL DEFAULT '',image TEXT NOT NULL DEFAULT '',active INTEGER NOT NULL DEFAULT 1,UNIQUE(hotel_id,room));
        CREATE TABLE IF NOT EXISTS menu(id INTEGER PRIMARY KEY,hotel_id INTEGER NOT NULL REFERENCES hotels(id),name_en TEXT NOT NULL DEFAULT '',name_he TEXT NOT NULL DEFAULT '',description_en TEXT NOT NULL DEFAULT '',description_he TEXT NOT NULL DEFAULT '',category TEXT NOT NULL DEFAULT 'food',price_cents INTEGER NOT NULL DEFAULT 0,image TEXT NOT NULL DEFAULT '',active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS activities(id INTEGER PRIMARY KEY,hotel_id INTEGER NOT NULL REFERENCES hotels(id),name_en TEXT NOT NULL DEFAULT '',name_he TEXT NOT NULL DEFAULT '',description_en TEXT NOT NULL DEFAULT '',description_he TEXT NOT NULL DEFAULT '',starts TEXT NOT NULL DEFAULT '',ends TEXT NOT NULL DEFAULT '',location TEXT NOT NULL DEFAULT '',capacity INTEGER NOT NULL DEFAULT 10,price_cents INTEGER NOT NULL DEFAULT 0,image TEXT NOT NULL DEFAULT '',active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS amenities(id INTEGER PRIMARY KEY,hotel_id INTEGER NOT NULL REFERENCES hotels(id),name_en TEXT NOT NULL DEFAULT '',name_he TEXT NOT NULL DEFAULT '',basis TEXT NOT NULL DEFAULT 'room',period TEXT NOT NULL DEFAULT 'daily',quota INTEGER NOT NULL DEFAULT 2,image TEXT NOT NULL DEFAULT '',active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS services(id INTEGER PRIMARY KEY,hotel_id INTEGER NOT NULL REFERENCES hotels(id),name_en TEXT NOT NULL DEFAULT '',name_he TEXT NOT NULL DEFAULT '',description_en TEXT NOT NULL DEFAULT '',description_he TEXT NOT NULL DEFAULT '',category TEXT NOT NULL DEFAULT 'info',image TEXT NOT NULL DEFAULT '',active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS stays(id INTEGER PRIMARY KEY,hotel_id INTEGER NOT NULL REFERENCES hotels(id),room_id INTEGER NOT NULL REFERENCES rooms(id),guests INTEGER NOT NULL,start TEXT NOT NULL,ends TEXT NOT NULL,zones TEXT NOT NULL,token TEXT NOT NULL UNIQUE,revoked INTEGER NOT NULL DEFAULT 0,created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY,hotel_id INTEGER NOT NULL REFERENCES hotels(id),stay_id INTEGER NOT NULL REFERENCES stays(id),amenity_id INTEGER NOT NULL REFERENCES amenities(id),qty INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'new',created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS food_orders(id INTEGER PRIMARY KEY,hotel_id INTEGER NOT NULL REFERENCES hotels(id),stay_id INTEGER NOT NULL REFERENCES stays(id),area TEXT NOT NULL,detail TEXT NOT NULL DEFAULT '',note TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'new',total_cents INTEGER NOT NULL,request_key TEXT UNIQUE NOT NULL,created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS food_lines(id INTEGER PRIMARY KEY,hotel_id INTEGER NOT NULL REFERENCES hotels(id),order_id INTEGER NOT NULL REFERENCES food_orders(id),product_id INTEGER NOT NULL REFERENCES menu(id),name TEXT NOT NULL,qty INTEGER NOT NULL,price_cents INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS bookings(id INTEGER PRIMARY KEY,hotel_id INTEGER NOT NULL REFERENCES hotels(id),stay_id INTEGER NOT NULL REFERENCES stays(id),activity_id INTEGER NOT NULL REFERENCES activities(id),qty INTEGER NOT NULL,created TEXT NOT NULL,cancelled_at TEXT,UNIQUE(stay_id,activity_id));
        CREATE TABLE IF NOT EXISTS folio(id INTEGER PRIMARY KEY,hotel_id INTEGER NOT NULL REFERENCES hotels(id),stay_id INTEGER NOT NULL REFERENCES stays(id),source TEXT NOT NULL,reference_id INTEGER NOT NULL,description TEXT NOT NULL,amount_cents INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'open',created TEXT NOT NULL,paid_at TEXT,UNIQUE(hotel_id,source,reference_id));
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,hotel_id INTEGER NOT NULL REFERENCES hotels(id),stay_id INTEGER NOT NULL REFERENCES stays(id),sender TEXT NOT NULL,body TEXT NOT NULL,created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,hotel_id INTEGER REFERENCES hotels(id),user_id INTEGER REFERENCES users(id),action TEXT NOT NULL,entity TEXT NOT NULL,entity_id TEXT NOT NULL,details TEXT NOT NULL,created TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_stays_hotel ON stays(hotel_id,room_id);
        CREATE INDEX IF NOT EXISTS idx_orders_hotel ON food_orders(hotel_id,id);
        CREATE INDEX IF NOT EXISTS idx_messages_hotel ON messages(hotel_id,stay_id,id);
        CREATE INDEX IF NOT EXISTS idx_folio_hotel ON folio(hotel_id,stay_id,status);
        ''')
        if 'image' not in {row['name'] for row in c.execute('PRAGMA table_info(rooms)')}:
            c.execute("ALTER TABLE rooms ADD COLUMN image TEXT NOT NULL DEFAULT ''")
        if 'cancelled_at' not in {row['name'] for row in c.execute('PRAGMA table_info(bookings)')}:
            c.execute('ALTER TABLE bookings ADD COLUMN cancelled_at TEXT')
        owner=c.execute('SELECT * FROM users WHERE role="owner" LIMIT 1').fetchone()
        if not owner:
            c.execute('INSERT INTO users(hotel_id,username,password_hash,display_name,role,permissions,created) VALUES(NULL,?,?,?,?,?,?)',('owner',hash_pass(OWNER_PASSWORD),'STAYOS Owner','owner',json.dumps(ALL_PERMS),utcnow()))
            print('STAYOS owner account created: username owner. Password: STAYOS_ADMIN_PASSWORD environment variable.',flush=True)
        if DEMO_MODE and not c.execute('SELECT 1 FROM hotels LIMIT 1').fetchone():
            seed_bayview(c)

def seed_bayview(c):
    """Fictional example content. No claim of affiliation or actual hotel inventory."""
    c.execute('''INSERT INTO hotels(slug,name,location,currency,language,welcome_en,welcome_he,description_en,description_he,hero_image,demo,created)
      VALUES(?,?,?,?,?,?,?,?,?,?,1,?)''',('bayview-demo','Bayview Hotel — Concept Demo','Sri Lanka · Unofficial concept','LKR','en','Your stay, beautifully connected.','החופשה שלכם, במקום אחד.','A fictional showcase for a Bayview hotel partnership proposal. Not affiliated with the hotel.','הדגמה לא רשמית להצעת שיתוף פעולה, לא אתר של המלון.','resort-hero.webp',utcnow()))
    hid=c.execute('SELECT id FROM hotels WHERE slug=?',('bayview-demo',)).fetchone()['id']
    for en,he,desc,size,maxg,img in [('Garden Deluxe','חדר דלוקס גן','Warm woods, garden views and quiet mornings','36 m²',3,'room-garden.webp'),('Ocean View Suite','סוויטה עם נוף לים','Panoramic coastline, generous living space','58 m²',4,'room-ocean.webp'),('Private Pool Villa','וילה עם בריכה פרטית','Intimate tropical retreat with a private pool','92 m²',5,'room-villa.webp')]:
        c.execute('INSERT INTO room_types(hotel_id,name_en,name_he,description_en,description_he,size,max_guests,image) VALUES(?,?,?,?,?,?,?,?)',(hid,en,he,desc,{'Garden Deluxe':'עיצוב טרופי חמים ונוף ירוק לגינה','Ocean View Suite':'סוויטה מרווחת עם נוף פתוח לים','Private Pool Villa':'פרטיות מלאה ובריכה אישית באווירה טרופית'}.get(en,desc),size,maxg,img))
    ids=[x['id'] for x in c.execute('SELECT id FROM room_types WHERE hotel_id=? ORDER BY id',(hid,)).fetchall()]
    for num,typ in [('101',ids[0]),('102',ids[0]),('204',ids[1]),('305',ids[2])]:c.execute('INSERT INTO rooms(hotel_id,room,room_type_id,floor) VALUES(?,?,?,?)',(hid,num,typ,num[0]))
    for name,he,desc,cat,price,img in [('Fresh Coconut','קוקוס טרי','Young coconut served chilled','drinks',155000,'coconut.webp'),('Tropical Smoothie','שייק טרופי','Mango & pineapple','drinks',185000,'smoothie.webp'),('Ceylon Iced Tea','תה ציילון קר','Tea, mint and lime','drinks',120000,'tea.webp'),('Club Sandwich','קלאב סנדוויץ׳','Grilled sandwich with fries','food',290000,'sandwich.webp'),('Tropical Fruit Plate','מגש פירות','Fresh seasonal fruit','food',195000,'fruit.webp'),('Vegetable Curry','קארי ירקות','Sri Lankan inspired curry','food',320000,'curry.webp'),('Poolside Fries','צ׳יפס לבריכה','Crispy golden fries','snacks',145000,'fries.webp')]:
        c.execute('INSERT INTO menu(hotel_id,name_en,name_he,description_en,description_he,category,price_cents,image) VALUES(?,?,?,?,?,?,?,?)',(hid,name,he,desc,{'Fresh Coconut':'קוקוס צעיר קר ומרענן','Tropical Smoothie':'שייק מנגו ואננס','Ceylon Iced Tea':'תה קר עם נענע וליים','Club Sandwich':'כריך קלוי לצד צ׳יפס','Tropical Fruit Plate':'מבחר פירות טרופיים טריים','Vegetable Curry':'קארי ירקות בסגנון סרי לנקי','Poolside Fries':'צ׳יפס זהוב ופריך'}.get(name,desc),cat,price,img))
    for en,he,basis,period,quota,img in [('Fresh Towels','מגבות נקיות','guest','daily',2,'resort-pool.webp'),('Water Bottles','בקבוקי מים','guest','daily',2,'coconut.webp'),('Extra Pillow','כרית נוספת','room','stay',2,'room-garden.webp'),('Baby Cot','מיטת תינוק','room','stay',1,'room-villa.webp')]:
        c.execute('INSERT INTO amenities(hotel_id,name_en,name_he,basis,period,quota,image) VALUES(?,?,?,?,?,?,?)',(hid,en,he,basis,period,quota,img))
    for name,he,desc,cat,img in [('Signature Spa','ספא המלון','Relaxing massages and wellness rituals','spa','wellness-spa.webp'),('Ocean Dining','מסעדת המלון','Fresh seasonal flavours with a sea view','dining','dining.webp'),('Infinity Pool','בריכת אינפיניטי','A tranquil oasis in the sun','facilities','resort-pool.webp')]:
        c.execute('INSERT INTO services(hotel_id,name_en,name_he,description_en,description_he,category,image) VALUES(?,?,?,?,?,?,?)',(hid,name,he,desc,{'Signature Spa':'עיסויים מרגיעים וטיפולי גוף','Ocean Dining':'מנות טריות ונוף מרהיב לים','Infinity Pool':'בריכה שלווה בלב הנוף הטרופי'}.get(name,desc),cat,img))
    now=datetime.now(timezone.utc)
    for day,hour,name,he,price,img,loc in [(1,8,'Sunrise Yoga','יוגה בזריחה',0,'yoga.webp','Beach Deck'),(1,16,'Ceylon Tea Ceremony','טקס תה ציילוני',140000,'dining.webp','Garden Lounge'),(2,17,'Sunset Wellness','רוגע בשקיעה',240000,'wellness-spa.webp','Spa Pavilion')]:
        d=(now+timedelta(days=day)).replace(hour=hour,minute=0,second=0,microsecond=0)
        c.execute('INSERT INTO activities(hotel_id,name_en,name_he,description_en,description_he,starts,ends,location,capacity,price_cents,image) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(hid,name,he,'An inspiring resort experience','חוויה מיוחדת במלון',d.isoformat(),(d+timedelta(hours=1)).isoformat(),loc,12,price,img))
    # This is a synthetic sample guest pass, visible only to logged-in hotel management.
    room=c.execute('SELECT id FROM rooms WHERE hotel_id=? AND room=?',(hid,'101')).fetchone()['id']
    c.execute('INSERT INTO stays(hotel_id,room_id,guests,start,ends,zones,token,created) VALUES(?,?,?,?,?,?,?,?)',(hid,room,2,(now-timedelta(days=1)).isoformat(),(now+timedelta(days=14)).isoformat(),json.dumps(['requests','orders','activities','chat']),secrets.token_urlsafe(32),utcnow()))

class Bad(Exception):
    def __init__(self,msg,status=400):self.msg=str(msg);self.status=status

class Handler(BaseHTTPRequestHandler):
    server_version='STAYOS/3.2'
    def log_message(self,fmt,*args): print('HTTP',self.address_string(),fmt%args,flush=True)
    def send_bytes(self,data,content_type='application/octet-stream',status=200,extra=None):
        active_conn=CURRENT_CONN.get()
        if active_conn is not None:active_conn.commit()
        self.send_response(status);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(data)))
        self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer')
        self.send_header('X-Frame-Options','DENY');self.send_header('Cache-Control','no-store')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        for k,v in (extra or {}).items():self.send_header(k,v)
        self.end_headers();self.wfile.write(data)
    def ok(self,data,status=200,extra=None):self.send_bytes(json.dumps(data,ensure_ascii=False).encode('utf-8'),'application/json; charset=utf-8',status,extra)
    def err(self,msg,status=400):self.ok({'error':str(msg)},status)
    def body(self):
        if self.headers.get('Content-Type','').split(';')[0].lower()!='application/json':raise Bad('Expected application/json',415)
        n=int(self.headers.get('Content-Length','0'))
        if n<0 or n>65536:raise Bad('Body too large',413)
        try:v=json.loads(self.rfile.read(n))
        except Exception:raise Bad('Invalid JSON')
        if not isinstance(v,dict):raise Bad('Expected JSON object')
        return v
    def raw(self,maxsize=7*1024*1024):
        n=int(self.headers.get('Content-Length','0'))
        if n<=0 or n>maxsize:raise Bad('Invalid image size',413)
        return self.rfile.read(n)
    def user(self,c):
        import http.cookies
        ck=http.cookies.SimpleCookie()
        try:ck.load(self.headers.get('Cookie',''))
        except Exception:return None
        token=ck.get('stayos_v3')
        if not token:return None
        digest=hashlib.sha256(token.value.encode()).hexdigest()
        return c.execute('SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>? AND u.active=1',(digest,utcnow())).fetchone()
    def require(self,c,permission=None,hotel_id=None,manager_only=False,write=False):
        user=self.user(c)
        if not user:raise Bad('Staff login required',401)
        if user['role']=='owner':return user
        if hotel_id is not None and user['hotel_id']!=hotel_id:raise Bad('Access denied for this hotel',403)
        if manager_only and user['role']!='manager':raise Bad('Hotel admin required',403)
        if permission and user['role']!='manager':
            perms=json.loads(user['permissions'])
            if not (permission in perms or 'edit:'+permission in perms or (not write and 'view:'+permission in perms)):
                raise Bad('Permission denied',403)
        return user
    def csrf(self):
        origin=self.headers.get('Origin')
        if origin:
            p=urlparse(origin);host=self.headers.get('Host','').lower()
            # Railway proxy terminates TLS; Origin must still match Host.
            if p.netloc.lower()!=host or p.scheme not in ('http','https'):raise Bad('Origin mismatch',403)
        if 'Cookie' in self.headers and self.headers.get('Sec-Fetch-Site')=='cross-site':raise Bad('Cross-site request blocked',403)
    def hotel(self,c,slug):
        h=c.execute('SELECT * FROM hotels WHERE slug=? AND active=1',(slug,)).fetchone()
        if not h:raise Bad('Hotel not found',404)
        return h
    def guest(self,c,hotel_id,token):
        s=c.execute('SELECT s.*,r.room FROM stays s JOIN rooms r ON s.room_id=r.id WHERE s.hotel_id=? AND s.token=?',(hotel_id,token)).fetchone()
        if not s or s['revoked']:raise Bad('Invalid or revoked guest pass',401)
        now=datetime.now(timezone.utc)
        if not parse_dt(s['start'])<=now<=parse_dt(s['ends']):raise Bad('Guest pass expired or not yet active',401)
        return s
    def url(self,path):
        explicit=os.environ.get('STAYOS_PUBLIC_URL',os.environ.get('STAYOS_GUEST_BASE_URL','')).rstrip('/')
        if not explicit and os.environ.get('RAILWAY_PUBLIC_DOMAIN'):explicit='https://'+os.environ['RAILWAY_PUBLIC_DOMAIN']
        if not explicit: explicit='http://localhost:'+str(PORT)
        return explicit+path
    def static(self,path):
        if path in ('/','/owner','/manager','/login') or re.fullmatch(r'/h/[a-z0-9-]+(?:/guest|/preview)?',path):fn=ROOT/'static'/'index.html'
        elif path=='/manifest.webmanifest':fn=ROOT/'static'/'manifest.webmanifest'
        elif path=='/sw.js':fn=ROOT/'static'/'sw.js'
        elif path.startswith('/static/'):
            rel=path[8:];fn=(ROOT/'static'/rel).resolve()
            if not fn.is_relative_to((ROOT/'static').resolve()):raise Bad('Invalid path',404)
        else:raise Bad('Not found',404)
        if not fn.is_file():raise Bad('Not found',404)
        mime=mimetypes.guess_type(str(fn))[0] or 'application/octet-stream'
        self.send_bytes(fn.read_bytes(),mime)
    def get(self,path,q):
        if path=='/health':return self.ok({'ok':True,'version':'3.2.0'})
        if path.startswith('/media/'):
            m=re.fullmatch(r'/media/(\d+)/([0-9a-f]{24}\.webp)',path)
            if not m:raise Bad('Not found',404)
            fn=MEDIA/m.group(1)/m.group(2)
            if not fn.is_file():raise Bad('Not found',404)
            return self.send_bytes(fn.read_bytes(),'image/webp')
        if path=='/api/auth/myhotel':
            with conn() as c:
                u=self.require(c)
                if u['role']=='owner':raise Bad('Owner does not have a single hotel',400)
                h=c.execute('SELECT slug,name FROM hotels WHERE id=? AND active=1',(u['hotel_id'],)).fetchone()
                if not h:raise Bad('Hotel account not available',404)
                return self.ok(dict(h))
        if path in ('/api/auth/me','/api/auth'):
            with conn() as c:
                u=self.user(c)
                if not u:return self.ok({'authenticated':False})
                return self.ok({'authenticated':True,'user':{'id':u['id'],'username':u['username'],'display_name':u['display_name'],'role':u['role'],'hotel_id':u['hotel_id'],'permissions':ALL_PERMS if u['role'] in ('owner','manager') else json.loads(u['permissions'])}})
        if path=='/api/owner/hotels':
            with conn() as c:
                self.require(c,manager_only=True)
                if self.user(c)['role']!='owner':raise Bad('Owner only',403)
                return self.ok([dict(h) for h in c.execute('SELECT * FROM hotels ORDER BY id')])
        mh=re.fullmatch(r'/api/h/([a-z0-9-]+)/(.*)',path)
        if mh:
            slug,rest=mh.group(1),mh.group(2)
            with conn() as c:
                h=self.hotel(c,slug);hid=h['id']
                if rest=='public':
                    hotel=dict(h)
                    return self.ok({'hotel':hotel,'room_types':self.rows(c,'room_types',hid,public=True),'menu':self.rows(c,'menu',hid,public=True),'amenities':self.rows(c,'amenities',hid,public=True),'activities':self.rows(c,'activities',hid,public=True),'services':self.rows(c,'services',hid,public=True)})
                if rest.startswith('guest/'):
                    bits=rest.split('/')
                    if len(bits)<3:raise Bad('Not found',404)
                    s=self.guest(c,hid,bits[1]);action='/'.join(bits[2:])
                    if action=='manifest':
                        manifest={'name':h['name']+' Guest Services','short_name':h['name'][:18],
                          'id':'/app/'+slug+'/'+bits[1][:18],
                          'start_url':'/h/'+slug+'/guest?token='+quote(bits[1]),
                          'scope':'/','display':'standalone','theme_color':h['primary_color'],
                          'background_color':'#f8f6f1','icons':[{'src':'/static/assets/app-icon-192.png','sizes':'192x192','type':'image/png','purpose':'any maskable'},{'src':'/static/assets/app-icon-512.png','sizes':'512x512','type':'image/png','purpose':'any maskable'}]}
                        return self.send_bytes(json.dumps(manifest).encode(),'application/manifest+json; charset=utf-8')
                    if action=='session':
                        room=c.execute('SELECT rt.*,r.image AS physical_room_image FROM rooms r JOIN room_types rt ON rt.id=r.room_type_id WHERE r.id=? AND r.hotel_id=?',(s['room_id'],hid)).fetchone()
                        room_data=dict(room) if room else None
                        if room_data and room_data.get('physical_room_image'):room_data['image']=room_data['physical_room_image']
                        return self.ok({'stay':{'room':s['room'],'guests':s['guests'],'ends':s['ends'],'zones':json.loads(s['zones'])},'room_type':room_data})
                    if action=='my':
                        return self.ok({'requests':[dict(x) for x in c.execute('''SELECT q.*,a.name_en,a.name_he FROM requests q JOIN amenities a ON a.id=q.amenity_id WHERE q.hotel_id=? AND q.stay_id=? ORDER BY q.id DESC''',(hid,s['id']))], 'orders':[dict(x) for x in c.execute('SELECT * FROM food_orders WHERE hotel_id=? AND stay_id=? ORDER BY id DESC',(hid,s['id']))], 'folio':[dict(x) for x in c.execute('SELECT * FROM folio WHERE hotel_id=? AND stay_id=? ORDER BY id DESC',(hid,s['id']))], 'bookings':[dict(x) for x in c.execute('SELECT b.*,a.name_en,a.name_he FROM bookings b JOIN activities a ON a.id=b.activity_id WHERE b.hotel_id=? AND b.stay_id=? AND b.cancelled_at IS NULL',(hid,s['id']))]})
                    if action=='chat':return self.ok([dict(m) for m in c.execute('SELECT id,sender,body,created FROM messages WHERE hotel_id=? AND stay_id=? ORDER BY id LIMIT 500',(hid,s['id']))])
                    raise Bad('Not found',404)
                if not rest.startswith('manage/'):raise Bad('Not found',404)
                part=rest[7:]
                if part in FIELDS:
                    self.require(c,RESOURCE_PERM[part],hid)
                    return self.ok(self.rows(c,part,hid))
                if part=='branding':self.require(c,'branding',hid);return self.ok(dict(h))
                if part=='media':
                    self.require(c,'media',hid)
                    folder=MEDIA/str(hid)
                    return self.ok(['/media/'+str(hid)+'/'+p.name for p in sorted(folder.glob('*.webp'),key=lambda p:p.stat().st_mtime,reverse=True)] if folder.is_dir() else [])
                if part=='dashboard':
                    u=self.require(c,None,hid)
                    dash={k:c.execute('SELECT COUNT(*) FROM '+table+' WHERE hotel_id=?',(hid,)).fetchone()[0] for k,table in [('stays','stays'),('requests','requests'),('orders','food_orders'),('activities','activities'),('rooms','rooms')] } | {'open_folio':c.execute('SELECT COALESCE(SUM(amount_cents),0) FROM folio WHERE hotel_id=? AND status="open"',(hid,)).fetchone()[0], 'pending_requests':c.execute('SELECT COUNT(*) FROM requests WHERE hotel_id=? AND status="new"',(hid,)).fetchone()[0], 'pending_orders':c.execute('SELECT COUNT(*) FROM food_orders WHERE hotel_id=? AND status="new"',(hid,)).fetchone()[0], 'latest_guest_message':c.execute('SELECT COALESCE(MAX(id),0) FROM messages WHERE hotel_id=? AND sender="guest"',(hid,)).fetchone()[0]}
                    if u['role']=='staff':
                        perm=json.loads(u['permissions'])
                        for k,required in [('open_folio','folio'),('stays','guests'),('pending_requests','requests'),('requests','requests'),('pending_orders','orders'),('orders','orders'),('latest_guest_message','chat'),('rooms','rooms'),('activities','activities')]:
                            if not (required in perm or 'view:'+required in perm or 'edit:'+required in perm):dash[k]=None
                    return self.ok(dash)
                if part=='events':
                    # Permission-scoped activity feed. Unlike pending counters, an event ID
                    # stays new even if a different item was marked completed.
                    u=self.require(c,None,hid)
                    perms=set(json.loads(u['permissions'])) if u['role']=='staff' else set(ALL_PERMS)
                    def can_view(permission):
                        return any(p in perms for p in (permission,'view:'+permission,'edit:'+permission))
                    feeds=[]
                    if can_view('requests'):
                        feeds += [dict(row)|{'type':'request','kind':'Guest service','label':row['item']}
                                  for row in c.execute('''SELECT q.id,q.created,r.room,a.name_en AS item,q.qty,q.status
                                      FROM requests q JOIN stays s ON s.id=q.stay_id
                                      JOIN rooms r ON r.id=s.room_id JOIN amenities a ON a.id=q.amenity_id
                                      WHERE q.hotel_id=? ORDER BY q.id DESC LIMIT 30''',(hid,))]
                    if can_view('orders'):
                        feeds += [dict(row)|{'type':'order','kind':'Food order','label':row['label']}
                                  for row in c.execute('''SELECT o.id,o.created,r.room,o.area,o.detail,o.note,o.status,o.total_cents,
                                      o.area || CASE WHEN o.detail != '' THEN ' · ' || o.detail ELSE '' END AS label
                                      FROM food_orders o JOIN stays s ON s.id=o.stay_id
                                      JOIN rooms r ON r.id=s.room_id
                                      WHERE o.hotel_id=? ORDER BY o.id DESC LIMIT 30''',(hid,))]
                    if can_view('activities'):
                        feeds += [dict(row)|{'type':'booking','kind':'Activity booking','label':row['label']}
                                  for row in c.execute('''SELECT b.id,b.created,r.room,a.name_en AS label,b.qty,a.price_cents
                                      FROM bookings b JOIN stays s ON s.id=b.stay_id
                                      JOIN rooms r ON r.id=s.room_id JOIN activities a ON a.id=b.activity_id
                                      WHERE b.hotel_id=? AND b.cancelled_at IS NULL ORDER BY b.id DESC LIMIT 30''',(hid,))]
                    if can_view('chat'):
                        feeds += [dict(row)|{'type':'message','kind':'Guest message','label':row['label']}
                                  for row in c.execute('''SELECT m.id,m.created,r.room,substr(m.body,1,75) AS label
                                      FROM messages m JOIN stays s ON s.id=m.stay_id
                                      JOIN rooms r ON r.id=s.room_id WHERE m.hotel_id=? AND m.sender='guest'
                                      ORDER BY m.id DESC LIMIT 30''',(hid,))]
                    # Resolve order line summaries without exposing another hotel's items.
                    for event in feeds:
                        if event['type']=='order':
                            event['items']=[dict(line) for line in c.execute('SELECT name,qty FROM food_lines WHERE hotel_id=? AND order_id=? ORDER BY id',(hid,event['id']))]
                            event['label']=', '.join(str(x['qty'])+'× '+x['name'] for x in event['items'])
                        elif event['type']=='request':event['label']=str(event['qty'])+'× '+event['label']
                        elif event['type']=='booking':event['label']=str(event['qty'])+'× '+event['label']
                    return self.ok(sorted(feeds,key=lambda e:(e['created'],e['id']),reverse=True)[:80])
                if part=='room-history':
                    u=self.require(c,None,hid)
                    perms=set(json.loads(u['permissions'])) if u['role']=='staff' else set(ALL_PERMS)
                    permitted=lambda permission: any(p in perms for p in (permission,'view:'+permission,'edit:'+permission))
                    if not any(permitted(p) for p in ('guests','requests','orders','activities','chat','folio')):
                        raise Bad('Permission denied',403)
                    room_names=[str(r['room']) for r in c.execute('SELECT room FROM rooms WHERE hotel_id=? ORDER BY room',(hid,))]
                    room=str(q.get('room',[''])[0]).strip()
                    if len(room)>40:raise Bad('Invalid room',400)
                    if not room:return self.ok({'rooms':room_names,'room':None,'entries':[],'stays':[]})
                    room_row=c.execute('SELECT id,room FROM rooms WHERE hotel_id=? AND room=?',(hid,room)).fetchone()
                    if not room_row:return self.ok({'rooms':room_names,'room':room,'entries':[],'stays':[]})
                    room_id=room_row['id']
                    stays=[dict(row) for row in c.execute('SELECT id,guests,start,ends,revoked FROM stays WHERE hotel_id=? AND room_id=? ORDER BY id DESC',(hid,room_id))] if permitted('guests') else []
                    entries=[]
                    if permitted('orders'):
                        for row in c.execute('''SELECT o.id,o.stay_id,o.created,o.area,o.detail,o.note,o.status,o.total_cents
                           FROM food_orders o JOIN stays s ON s.id=o.stay_id WHERE o.hotel_id=? AND s.hotel_id=? AND s.room_id=? ORDER BY o.id DESC LIMIT 300''',(hid,hid,room_id)):
                            e=dict(row)
                            e.update(type='order',items=[dict(x) for x in c.execute('SELECT name,qty,price_cents FROM food_lines WHERE hotel_id=? AND order_id=? ORDER BY id',(hid,e['id']))])
                            entries.append(e)
                    if permitted('requests'):
                        entries += [dict(row)|{'type':'request'} for row in c.execute('''SELECT q.id,q.stay_id,q.created,q.qty,q.status,a.name_en AS name_en,a.name_he AS name_he
                            FROM requests q JOIN stays s ON s.id=q.stay_id JOIN amenities a ON a.id=q.amenity_id
                            WHERE q.hotel_id=? AND s.hotel_id=? AND s.room_id=? ORDER BY q.id DESC LIMIT 300''',(hid,hid,room_id))]
                    if permitted('activities'):
                        entries += [dict(row)|{'type':'booking','status':'cancelled' if row['cancelled_at'] else 'booked'} for row in c.execute('''SELECT b.id,b.stay_id,b.created,b.qty,b.cancelled_at,a.name_en AS name_en,a.name_he AS name_he,a.starts,a.location,a.price_cents
                            FROM bookings b JOIN stays s ON s.id=b.stay_id JOIN activities a ON a.id=b.activity_id
                            WHERE b.hotel_id=? AND s.hotel_id=? AND s.room_id=? ORDER BY b.id DESC LIMIT 300''',(hid,hid,room_id))]
                    if permitted('chat'):
                        entries += [dict(row)|{'type':'message'} for row in c.execute('''SELECT m.id,m.stay_id,m.created,m.body,m.sender FROM messages m JOIN stays s ON s.id=m.stay_id
                            WHERE m.hotel_id=? AND s.hotel_id=? AND s.room_id=? ORDER BY m.id DESC LIMIT 300''',(hid,hid,room_id))]
                    if permitted('folio'):
                        entries += [dict(row)|{'type':'charge'} for row in c.execute('''SELECT f.id,f.stay_id,f.created,f.description,f.amount_cents,f.status,f.source
                            FROM folio f JOIN stays s ON s.id=f.stay_id
                            WHERE f.hotel_id=? AND s.hotel_id=? AND s.room_id=? ORDER BY f.id DESC LIMIT 300''',(hid,hid,room_id))]
                    entries.sort(key=lambda e:(e['created'],e['id']),reverse=True)
                    return self.ok({'rooms':room_names,'room':room,'entries':entries[:600],'stays':stays})
                if part=='bookings':
                    self.require(c,'activities',hid)
                    return self.ok([dict(row) for row in c.execute('''SELECT b.id,b.created,b.qty,b.activity_id,
                            a.name_en AS activity,a.name_he AS activity_he,a.starts,
                            r.room,b.qty * a.price_cents AS total_cents,b.cancelled_at
                            FROM bookings b JOIN activities a ON a.id=b.activity_id
                            JOIN stays s ON s.id=b.stay_id JOIN rooms r ON r.id=s.room_id
                            WHERE b.hotel_id=? ORDER BY b.id DESC''',(hid,))])
                if part=='stays':
                    self.require(c,'guests',hid)
                    return self.ok([dict(s) for s in c.execute('SELECT s.id,s.hotel_id,s.room_id,r.room,s.guests,s.start,s.ends,s.zones,s.revoked,s.created,s.token FROM stays s JOIN rooms r ON s.room_id=r.id WHERE s.hotel_id=? ORDER BY s.id DESC',(hid,))])
                if part=='requests':
                    self.require(c,'requests',hid)
                    return self.ok([dict(x) for x in c.execute('SELECT q.*,r.room,a.name_en AS item FROM requests q JOIN stays s ON s.id=q.stay_id JOIN rooms r ON r.id=s.room_id JOIN amenities a ON a.id=q.amenity_id WHERE q.hotel_id=? ORDER BY q.id DESC',(hid,))])
                if part=='orders':
                    self.require(c,'orders',hid)
                    rows=c.execute('SELECT o.*,r.room FROM food_orders o JOIN stays s ON s.id=o.stay_id JOIN rooms r ON r.id=s.room_id WHERE o.hotel_id=? ORDER BY o.id DESC',(hid,)).fetchall()
                    return self.ok([dict(o)|{'lines':[dict(x) for x in c.execute('SELECT name,qty,price_cents FROM food_lines WHERE hotel_id=? AND order_id=?',(hid,o['id']))]} for o in rows])
                if part=='folio':
                    self.require(c,'folio',hid)
                    return self.ok([dict(x) for x in c.execute('SELECT f.*,r.room,fo.area AS delivery_area,fo.detail AS delivery_detail,fo.note AS delivery_note FROM folio f JOIN stays s ON s.id=f.stay_id JOIN rooms r ON r.id=s.room_id LEFT JOIN food_orders fo ON fo.hotel_id=f.hotel_id AND fo.id=f.reference_id AND f.source="food" WHERE f.hotel_id=? ORDER BY f.id DESC',(hid,))])
                if part=='chat':
                    self.require(c,'chat',hid)
                    stay_id=int(q.get('stay_id',[0])[0])
                    if stay_id:
                        if not c.execute('SELECT 1 FROM stays WHERE hotel_id=? AND id=?',(hid,stay_id)).fetchone():raise Bad('Invalid stay',404)
                        return self.ok([dict(x) for x in c.execute('SELECT * FROM messages WHERE hotel_id=? AND stay_id=? ORDER BY id',(hid,stay_id))])
                    return self.ok([dict(x) for x in c.execute('''SELECT s.id AS stay_id,r.room,MAX(m.id) AS latest_id,COUNT(m.id) AS count FROM stays s JOIN rooms r ON r.id=s.room_id JOIN messages m ON m.stay_id=s.id AND m.hotel_id=s.hotel_id WHERE s.hotel_id=? GROUP BY s.id ORDER BY latest_id DESC''',(hid,))])
                if part=='staff':
                    self.require(c,None,hid,manager_only=True)
                    return self.ok([{'id':u['id'],'username':u['username'],'display_name':u['display_name'],'role':u['role'],'permissions':json.loads(u['permissions']),'active':u['active']} for u in c.execute('SELECT * FROM users WHERE hotel_id=? ORDER BY id',(hid,))])
                if part=='audit':
                    self.require(c,None,hid,manager_only=True)
                    return self.ok([dict(x) for x in c.execute('SELECT a.*,u.display_name FROM audit a LEFT JOIN users u ON u.id=a.user_id WHERE a.hotel_id=? ORDER BY a.id DESC LIMIT 150',(hid,))])
                mq=re.fullmatch(r'stays/(\d+)/qr',part)
                if mq:
                    self.require(c,'guests',hid)
                    s=c.execute('SELECT token FROM stays WHERE id=? AND hotel_id=?',(int(mq.group(1)),hid)).fetchone()
                    if not s:raise Bad('Stay not found',404)
                    url=self.url('/h/'+slug+'/guest?token='+quote(s['token']))
                    img=qrcode.make(url);buf=io.BytesIO();img.save(buf,format='PNG')
                    return self.send_bytes(buf.getvalue(),'image/png')
                raise Bad('Not found',404)
        return self.static(path)
    def rows(self,c,table,hid,public=False):
        filt=' AND active=1' if public else ''
        return [dict(x) for x in c.execute('SELECT * FROM '+table+' WHERE hotel_id=?'+filt+' ORDER BY id DESC',(hid,))]
    def validate(self,c,hid,resource,data,existing=None):
        if resource not in FIELDS:raise Bad('Unknown catalog',404)
        res={}
        for k,typ in FIELDS[resource].items():
            if k not in data and existing is not None:continue
            v=data.get(k,{'bool':1,'int':1,'money':0}.get(typ,''))
            if typ in ('str','text','image','date'):
                v=str(v).strip()
                limit=500 if typ=='text' else 180
                if len(v)>limit:raise Bad('Field too long: '+k)
                if typ=='image' and v and not (re.fullmatch(r'[\w-]+\.(?:webp|png|svg)',v) or re.fullmatch(r'/media/'+str(hid)+r'/[0-9a-f]{24}\.webp',v)):
                    raise Bad('Invalid image location')
                if typ=='date' and v:parse_dt(v)
            elif typ in ('int','money'):
                try:v=int(v)
                except Exception:raise Bad('Invalid number: '+k)
                if v<0 or v>100000000:raise Bad('Number out of range: '+k)
            elif typ=='bool':v=int(bool(v))
            res[k]=v
        for k in ('name_en','room'):
            if k in res and not res[k]:raise Bad(k+' required')
        if resource=='rooms':
            rid=res.get('room_type_id',existing['room_type_id'] if existing else 0)
            if not c.execute('SELECT 1 FROM room_types WHERE hotel_id=? AND id=? AND active=1',(hid,rid)).fetchone():raise Bad('Invalid room type')
        if resource=='activities':
            start=res.get('starts',existing['starts'] if existing else '')
            end=res.get('ends',existing['ends'] if existing else '')
            if not start or not end or parse_dt(start)>=parse_dt(end):raise Bad('Activity end must follow start')
        if resource=='room_types' and 'max_guests' in res and res['max_guests']<1:raise Bad('Max guests must be positive')
        if resource=='activities' and 'capacity' in res and res['capacity']<1:raise Bad('Capacity must be positive')
        if resource=='amenities':
            if 'basis' in res and res['basis'] not in ('guest','room'):raise Bad('Invalid quota basis')
            if 'period' in res and res['period'] not in ('daily','stay'):raise Bad('Invalid quota period')
            if 'quota' in res and res['quota']<1:raise Bad('Quota must be positive')
        return res
    def post(self,path,q,data=None):
        if path=='/api/auth/login':
            b=data or {};username=str(b.get('username','')).strip().lower();pw=str(b.get('password',''))
            if not username or len(username)>180 or len(pw)>256:raise Bad('Invalid credentials',401)
            with conn() as c:
                # Restrict brute force attempts per remote address in-process.
                ip=self.client_address[0]; now=time.monotonic()
                with FAILED_LOCK:
                    recent=[t for t in FAILURES.get(ip,[]) if now-t<600]
                    if len(recent)>=12:raise Bad('Too many login attempts',429)
                u=c.execute('SELECT * FROM users WHERE username=? AND active=1',(username,)).fetchone()
                if not u or not verify_pass(pw,u['password_hash']):
                    with FAILED_LOCK:FAILURES[ip]=recent+[now]
                    raise Bad('Invalid credentials',401)
                with FAILED_LOCK:FAILURES.pop(ip,None)
                token=secrets.token_urlsafe(32)
                expiry=(datetime.now(timezone.utc)+timedelta(hours=12)).isoformat()
                c.execute('INSERT INTO sessions(token_hash,user_id,expires) VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),u['id'],expiry))
                extra={'Set-Cookie':'stayos_v3='+token+'; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200'+('; Secure' if SECURE else '')}
                return self.ok({'ok':True,'role':u['role']},extra=extra)
        if path=='/api/auth/change-password':
            with conn() as c:
                u=self.require(c)
                old=str((data or {}).get('current_password',''))
                new=str((data or {}).get('new_password',''))
                if not verify_pass(old,u['password_hash']):raise Bad('Current password incorrect',403)
                if len(new)<12 or len(new)>256 or new==old:raise Bad('Choose a different password (12+ characters)')
                c.execute('UPDATE users SET password_hash=? WHERE id=?',(hash_pass(new),u['id']))
                c.execute('DELETE FROM sessions WHERE user_id=?',(u['id'],))
                audit(c,u['hotel_id'],u['id'],'change_password','staff',u['id'])
                return self.ok({'ok':True},extra={'Set-Cookie':'stayos_v3=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict'+('; Secure' if SECURE else '')})
        if path=='/api/auth/logout':
            with conn() as c:
                import http.cookies
                ck=http.cookies.SimpleCookie();ck.load(self.headers.get('Cookie',''));t=ck.get('stayos_v3')
                if t:c.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(t.value.encode()).hexdigest(),))
            return self.ok({'ok':True},extra={'Set-Cookie':'stayos_v3=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict'+('; Secure' if SECURE else '')})
        if path=='/api/owner/hotels':
            with conn() as c:
                u=self.require(c)
                if u['role']!='owner':raise Bad('Owner only',403)
                b=data or {};slug=str(b.get('slug','')).lower().strip();name=str(b.get('name','')).strip()
                if not re.fullmatch('[a-z][a-z0-9-]{2,39}',slug) or not 2<=len(name)<=90:raise Bad('Invalid hotel slug or name')
                c.execute('INSERT INTO hotels(slug,name,location,currency,language,description_en,created) VALUES(?,?,?,?,?,?,?)',(slug,name,str(b.get('location',''))[:120],str(b.get('currency','USD')).upper()[:3],str(b.get('language','en'))[:2],'Welcome to '+name,utcnow()))
                hid=c.execute('SELECT last_insert_rowid()').fetchone()[0];audit(c,hid,u['id'],'create','hotel',hid)
                return self.ok({'id':hid,'slug':slug},201)
        owner_update=re.fullmatch(r'/api/owner/hotels/(\d+)',path)
        if owner_update:
            with conn() as c:
                u=self.require(c)
                if u['role']!='owner':raise Bad('Owner only',403)
                hid=int(owner_update.group(1))
                new_status=int(bool((data or {}).get('active',1)))
                if not c.execute('SELECT 1 FROM hotels WHERE id=?',(hid,)).fetchone():raise Bad('Hotel not found',404)
                c.execute('UPDATE hotels SET active=? WHERE id=?',(new_status,hid))
                audit(c,hid,u['id'],'update','hotel',hid,'active='+str(new_status))
                return self.ok({'ok':True})
        mh=re.fullmatch(r'/api/h/([a-z0-9-]+)/(.*)',path)
        if not mh:raise Bad('Not found',404)
        slug,rest=mh.group(1),mh.group(2)
        with conn() as c:
            h=self.hotel(c,slug);hid=h['id'];b=data or {}
            if rest.startswith('guest/'):
                bits=rest.split('/')
                if len(bits)!=3:raise Bad('Not found',404)
                s=self.guest(c,hid,bits[1]);action=bits[2];zones=json.loads(s['zones'])
                if action=='request':
                    c.execute('BEGIN IMMEDIATE')
                    if 'requests' not in zones:raise Bad('Room services are disabled for this pass',403)
                    aid=int(b.get('amenity_id',0));qty=int(b.get('qty',1))
                    a=c.execute('SELECT * FROM amenities WHERE hotel_id=? AND id=? AND active=1',(hid,aid)).fetchone()
                    if not a or qty<1 or qty>20:raise Bad('Invalid request')
                    day=datetime.now(timezone.utc).date().isoformat()
                    filt=' AND created>=?' if a['period']=='daily' else ''
                    prev=c.execute('SELECT COALESCE(SUM(qty),0) FROM requests WHERE hotel_id=? AND stay_id=? AND amenity_id=?'+filt,((hid,s['id'],aid,day) if filt else (hid,s['id'],aid))).fetchone()[0]
                    allowed=a['quota']*(s['guests'] if a['basis']=='guest' else 1)
                    if prev+qty>allowed:raise Bad('Request quota reached')
                    c.execute('INSERT INTO requests(hotel_id,stay_id,amenity_id,qty,created) VALUES(?,?,?,?,?)',(hid,s['id'],aid,qty,utcnow()))
                    return self.ok({'ok':True},201)
                if action=='order':
                    c.execute('BEGIN IMMEDIATE')
                    if 'orders' not in zones:raise Bad('Dining disabled for this pass',403)
                    area=str(b.get('area','')).lower();detail=str(b.get('detail','')).strip();note=str(b.get('note','')).strip()
                    if area not in ('room','pool','lobby','beach','restaurant','garden') or len(detail)>90 or len(note)>300:raise Bad('Invalid delivery location')
                    if area!='room' and len(detail)<2:raise Bad('Specify your location, table or sunbed')
                    key=str(b.get('request_key',''))
                    if not re.fullmatch('[A-Za-z0-9_-]{16,100}',key):raise Bad('Invalid request key')
                    old=c.execute('SELECT * FROM food_orders WHERE hotel_id=? AND stay_id=? AND request_key=?',(hid,s['id'],key)).fetchone()
                    if old:return self.ok({'id':old['id'],'total_cents':old['total_cents'],'duplicate':True})
                    items=b.get('items',[])
                    if not isinstance(items,list) or not 1<=len(items)<=15:raise Bad('Select items')
                    lines=[];total=0;seen=set()
                    for i in items:
                        pid=int(i.get('product_id',0));qty=int(i.get('qty',0))
                        if pid in seen or qty<1 or qty>12:raise Bad('Invalid quantities')
                        seen.add(pid)
                        p=c.execute('SELECT * FROM menu WHERE hotel_id=? AND id=? AND active=1',(hid,pid)).fetchone()
                        if not p:raise Bad('Product not found')
                        total+=p['price_cents']*qty;lines.append((pid,p['name_en'],qty,p['price_cents']))
                    if total>250000000:raise Bad('Order is above pilot limit')
                    c.execute('INSERT INTO food_orders(hotel_id,stay_id,area,detail,note,total_cents,request_key,created) VALUES(?,?,?,?,?,?,?,?)',(hid,s['id'],area,detail,note,total,key,utcnow()))
                    oid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
                    for pid,name,qty,price in lines:c.execute('INSERT INTO food_lines(hotel_id,order_id,product_id,name,qty,price_cents) VALUES(?,?,?,?,?,?)',(hid,oid,pid,name,qty,price))
                    c.execute('INSERT INTO folio(hotel_id,stay_id,source,reference_id,description,amount_cents,created) VALUES(?,?,?,?,?,?,?)',(hid,s['id'],'food',oid,'Food order #'+str(oid),total,utcnow()))
                    return self.ok({'id':oid,'total_cents':total},201)
                if action=='book':
                    c.execute('BEGIN IMMEDIATE')
                    if 'activities' not in zones:raise Bad('Activities disabled for this pass',403)
                    aid=int(b.get('activity_id',0));qty=int(b.get('qty',1))
                    a=c.execute('SELECT * FROM activities WHERE hotel_id=? AND id=? AND active=1',(hid,aid)).fetchone()
                    if not a or qty<1 or qty>s['guests'] or parse_dt(a['starts'])<=datetime.now(timezone.utc):raise Bad('Invalid booking')
                    used=c.execute('SELECT COALESCE(SUM(qty),0) FROM bookings WHERE hotel_id=? AND activity_id=? AND cancelled_at IS NULL',(hid,aid)).fetchone()[0]
                    if used+qty>a['capacity']:raise Bad('No places available')
                    previous=c.execute('SELECT * FROM bookings WHERE hotel_id=? AND stay_id=? AND activity_id=?',(hid,s['id'],aid)).fetchone()
                    if previous and previous['cancelled_at'] is None:raise Bad('Already booked this activity',409)
                    if previous:
                        bid=previous['id']
                        c.execute('UPDATE bookings SET qty=?,created=?,cancelled_at=NULL WHERE id=? AND hotel_id=?',(qty,utcnow(),bid,hid))
                    else:
                        c.execute('INSERT INTO bookings(hotel_id,stay_id,activity_id,qty,created) VALUES(?,?,?,?,?)',(hid,s['id'],aid,qty,utcnow()))
                        bid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
                    total=qty*a['price_cents']
                    if total:
                        if previous:
                            c.execute('UPDATE folio SET description=?,amount_cents=?,status="open",paid_at=NULL,created=? WHERE hotel_id=? AND source="activity" AND reference_id=?',(a['name_en']+' × '+str(qty),total,utcnow(),hid,bid))
                        else:
                            c.execute('INSERT INTO folio(hotel_id,stay_id,source,reference_id,description,amount_cents,created) VALUES(?,?,?,?,?,?,?)',(hid,s['id'],'activity',bid,a['name_en']+' × '+str(qty),total,utcnow()))
                    return self.ok({'id':bid},201)
                if action=='cancel_booking':
                    if 'activities' not in zones:raise Bad('Activities disabled',403)
                    bid=int(b.get('booking_id',0))
                    bk=c.execute('SELECT * FROM bookings WHERE hotel_id=? AND stay_id=? AND id=?',(hid,s['id'],bid)).fetchone()
                    if not bk or bk['cancelled_at']:raise Bad('Booking not found',404)
                    paid=c.execute('SELECT 1 FROM folio WHERE hotel_id=? AND source="activity" AND reference_id=? AND status="paid"',(hid,bid)).fetchone()
                    if paid:raise Bad('Paid activity needs manual refund at reception')
                    c.execute('UPDATE folio SET status="void" WHERE hotel_id=? AND source="activity" AND reference_id=?',(hid,bid))
                    c.execute('UPDATE bookings SET cancelled_at=? WHERE hotel_id=? AND id=?',(utcnow(),hid,bid))
                    return self.ok({'ok':True})
                if action=='chat':
                    if 'chat' not in zones:raise Bad('Chat disabled for this pass',403)
                    msg=str(b.get('body','')).strip()
                    if not 1<=len(msg)<=1500:raise Bad('Message length invalid')
                    c.execute('INSERT INTO messages(hotel_id,stay_id,sender,body,created) VALUES(?,?,?,?,?)',(hid,s['id'],'guest',msg,utcnow()))
                    return self.ok({'ok':True},201)
                raise Bad('Not found',404)
            if not rest.startswith('manage/'):raise Bad('Not found',404)
            part=rest[7:]
            if part in FIELDS:
                u=self.require(c,RESOURCE_PERM[part],hid,write=True)
                vals=self.validate(c,hid,part,b)
                cols=list(vals);c.execute('INSERT INTO '+part+'(hotel_id,'+','.join(cols)+') VALUES(?,'+','.join('?'*len(cols))+')',(hid,*vals.values()))
                rid=c.execute('SELECT last_insert_rowid()').fetchone()[0];audit(c,hid,u['id'],'create',part,rid)
                return self.ok({'id':rid},201)
            if part=='branding':
                u=self.require(c,'branding',hid,write=True)
                allowed=['name','location','currency','language','welcome_en','welcome_he','description_en','description_he','primary_color','accent_color','hero_image','logo_image']
                vals={k:str(v).strip() for k,v in b.items() if k in allowed}
                if not vals:raise Bad('No changes')
                for key,value in vals.items():
                    if len(value)>500:raise Bad('Field too long')
                    if key.endswith('_color') and not re.fullmatch('#[0-9a-fA-F]{6}',value):raise Bad('Invalid hex color')
                    if key in ('hero_image','logo_image') and value and not (re.fullmatch(r'[\w-]+\.(webp|png|svg)',value) or re.fullmatch(r'/media/'+str(hid)+r'/[0-9a-f]{24}\.webp',value)):raise Bad('Invalid image')
                    if key=='currency' and not re.fullmatch('[A-Z]{3}',value):raise Bad('Use ISO currency code')
                c.execute('UPDATE hotels SET '+','.join(k+'=?' for k in vals)+' WHERE id=?',(*vals.values(),hid))
                audit(c,hid,u['id'],'update','branding',hid)
                return self.ok({'ok':True})
            if part=='stays':
                u=self.require(c,'guests',hid,write=True)
                rid=int(b.get('room_id',0));room=c.execute('SELECT r.*,rt.max_guests FROM rooms r JOIN room_types rt ON rt.id=r.room_type_id WHERE r.hotel_id=? AND r.id=? AND r.active=1',(hid,rid)).fetchone()
                guests=int(b.get('guests',1));start=parse_dt(b.get('start'));ends=parse_dt(b.get('ends'))
                if not room or not 1<=guests<=room['max_guests'] or start>=ends or (ends-start).days>90:raise Bad('Invalid stay details')
                if ends<=datetime.now(timezone.utc):raise Bad('Departure must be in future')
                zones=b.get('zones',['requests','orders','activities','chat'])
                if not isinstance(zones,list) or any(z not in ('requests','orders','activities','chat') for z in zones):raise Bad('Invalid zones')
                # Rotate prior access on same physical room only when the intervals overlap.
                for older in c.execute('SELECT id,start,ends FROM stays WHERE hotel_id=? AND room_id=? AND revoked=0',(hid,rid)).fetchall():
                    if start<parse_dt(older['ends']) and parse_dt(older['start'])<ends:c.execute('UPDATE stays SET revoked=1 WHERE hotel_id=? AND id=?',(hid,older['id']))
                token=secrets.token_urlsafe(32)
                c.execute('INSERT INTO stays(hotel_id,room_id,guests,start,ends,zones,token,created) VALUES(?,?,?,?,?,?,?,?)',(hid,rid,guests,start.isoformat(),ends.isoformat(),json.dumps(zones),token,utcnow()))
                sid=c.execute('SELECT last_insert_rowid()').fetchone()[0];audit(c,hid,u['id'],'create','stay',sid)
                return self.ok({'id':sid,'url':self.url('/h/'+slug+'/guest?token='+quote(token)),'token':token},201)
            if part=='demo-pass':
                self.require(c,'guests',hid,write=True)
                if not h['demo']:raise Bad('Demo guest access is available only for concept hotels',403)
                now=datetime.now(timezone.utc)
                for old in c.execute('''SELECT s.* FROM stays s JOIN rooms r ON r.id=s.room_id
                                        WHERE s.hotel_id=? AND r.room='101' AND s.revoked=0
                                        ORDER BY s.id DESC''',(hid,)).fetchall():
                    if parse_dt(old['start'])<=now<parse_dt(old['ends']) and 'orders' in json.loads(old['zones']):
                        return self.ok({'url':self.url('/h/'+slug+'/guest?token='+quote(old['token'])),
                                        'room':'101'})
                room=c.execute("SELECT id FROM rooms WHERE hotel_id=? AND room='101' AND active=1",(hid,)).fetchone()
                if not room:raise Bad('Create demo room 101 first',400)
                c.execute('UPDATE stays SET revoked=1 WHERE hotel_id=? AND room_id=?',(hid,room['id']))
                token=secrets.token_urlsafe(32)
                c.execute('''INSERT INTO stays(hotel_id,room_id,guests,start,ends,zones,token,created)
                             VALUES(?,?,?,?,?,?,?,?)''',(hid,room['id'],2,
                             (now-timedelta(minutes=5)).isoformat(),(now+timedelta(days=14)).isoformat(),
                             json.dumps(['requests','orders','activities','chat']),token,utcnow()))
                sid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
                audit(c,hid,self.user(c)['id'],'demo_pass','stay',sid)
                return self.ok({'url':self.url('/h/'+slug+'/guest?token='+quote(token)),'room':'101'},201)
            if part=='staff':
                u=self.require(c,None,hid,manager_only=True)
                username=str(b.get('username','')).lower().strip();name=str(b.get('display_name','')).strip()
                role=str(b.get('role','staff'))
                perms=b.get('permissions',[])
                password=str(b.get('password',''))
                if not re.fullmatch('[a-z0-9._@-]{4,90}',username) or not name or role not in ('staff','manager') or not isinstance(perms,list) or any(p not in VALID_PERMS for p in perms):raise Bad('Invalid staff details')
                if len(password)<12 or len(password)>256:raise Bad('Use password with at least 12 characters')
                if u['role']=='manager' and role=='manager':raise Bad('Only platform owner can assign hotel manager',403)
                c.execute('INSERT INTO users(hotel_id,username,password_hash,display_name,role,permissions,created) VALUES(?,?,?,?,?,?,?)',(hid,username,hash_pass(password),name,role,json.dumps(list(set(perms))),utcnow()))
                sid=c.execute('SELECT last_insert_rowid()').fetchone()[0];audit(c,hid,u['id'],'create','staff',sid)
                return self.ok({'id':sid},201)
            if part=='chat':
                u=self.require(c,'chat',hid,write=True)
                sid=int(b.get('stay_id',0));msg=str(b.get('body','')).strip()
                if not 1<=len(msg)<=1500 or not c.execute('SELECT 1 FROM stays WHERE hotel_id=? AND id=?',(hid,sid)).fetchone():raise Bad('Invalid chat target or message')
                c.execute('INSERT INTO messages(hotel_id,stay_id,sender,body,created) VALUES(?,?,?,?,?)',(hid,sid,'reception',msg,utcnow()))
                return self.ok({'ok':True},201)
            if part=='requests/status':
                u=self.require(c,'requests',hid,write=True);status=str(b.get('status',''))
                if status not in ('new','progress','done','cancelled'):raise Bad('Invalid status')
                cur=c.execute('UPDATE requests SET status=? WHERE hotel_id=? AND id=?',(status,hid,int(b.get('id',0))))
                if not cur.rowcount:raise Bad('Request not found',404)
                audit(c,hid,u['id'],'status','request',b.get('id'),status)
                return self.ok({'ok':True})
            if part=='orders/status':
                u=self.require(c,'orders',hid,write=True);status=str(b.get('status',''))
                if status not in ('new','preparing','on_way','delivered','cancelled'):raise Bad('Invalid status')
                oid=int(b.get('id',0));old=c.execute('SELECT * FROM food_orders WHERE hotel_id=? AND id=?',(hid,oid)).fetchone()
                if not old:raise Bad('Order not found',404)
                if old['status']=='cancelled' and status!='cancelled':raise Bad('Cannot reopen cancelled order')
                if status=='cancelled':
                    if old['status']=='delivered':raise Bad('Delivered order cannot be cancelled')
                    paid=c.execute('SELECT 1 FROM folio WHERE hotel_id=? AND source="food" AND reference_id=? AND status="paid"',(hid,oid)).fetchone()
                    if paid:raise Bad('Paid order cannot be voided')
                    c.execute('UPDATE folio SET status="void" WHERE hotel_id=? AND source="food" AND reference_id=?',(hid,oid))
                c.execute('UPDATE food_orders SET status=? WHERE hotel_id=? AND id=?',(status,hid,oid))
                audit(c,hid,u['id'],'status','order',oid,status)
                return self.ok({'ok':True})
            if part=='folio/settle':
                u=self.require(c,'folio',hid,write=True);sid=int(b.get('stay_id',0));method=str(b.get('method',''))
                if method not in ('cash','terminal','other'):raise Bad('Invalid payment method')
                if not c.execute('SELECT 1 FROM stays WHERE hotel_id=? AND id=?',(hid,sid)).fetchone():raise Bad('Stay not found',404)
                amount=c.execute('SELECT COALESCE(SUM(amount_cents),0) FROM folio WHERE hotel_id=? AND stay_id=? AND status="open"',(hid,sid)).fetchone()[0]
                c.execute('UPDATE folio SET status="paid",paid_at=? WHERE hotel_id=? AND stay_id=? AND status="open"',(utcnow()+' / '+method,hid,sid))
                audit(c,hid,u['id'],'settle','folio',sid,amount)
                return self.ok({'ok':True,'amount_cents':amount})
            raise Bad('Not found',404)
    def update(self,path,b):
        mh=re.fullmatch(r'/api/h/([a-z0-9-]+)/manage/([a-z_]+)/(\d+)',path)
        if not mh:raise Bad('Not found',404)
        slug,resource,id=mh.group(1),mh.group(2),int(mh.group(3))
        with conn() as c:
            h=self.hotel(c,slug);hid=h['id']
            if resource in FIELDS:
                u=self.require(c,RESOURCE_PERM[resource],hid,write=True)
                old=c.execute('SELECT * FROM '+resource+' WHERE hotel_id=? AND id=?',(hid,id)).fetchone()
                if not old:raise Bad('Item not found',404)
                vals=self.validate(c,hid,resource,b,old)
                if vals:c.execute('UPDATE '+resource+' SET '+','.join(k+'=?' for k in vals)+' WHERE hotel_id=? AND id=?',(*vals.values(),hid,id))
                audit(c,hid,u['id'],'update',resource,id)
                return self.ok({'ok':True})
            if resource=='staff':
                u=self.require(c,None,hid,manager_only=True)
                old=c.execute('SELECT * FROM users WHERE hotel_id=? AND id=?',(hid,id)).fetchone()
                if not old:raise Bad('Staff not found',404)
                if old['role']=='manager' and u['role']!='owner':raise Bad('Cannot edit a hotel manager',403)
                perms=b.get('permissions',json.loads(old['permissions']))
                if not isinstance(perms,list) or any(p not in VALID_PERMS for p in perms):raise Bad('Invalid permissions')
                active=int(bool(b.get('active',old['active'])))
                password=b.get('password','')
                fields={'permissions':json.dumps(list(set(perms))),'active':active}
                if password:
                    if len(str(password))<12:raise Bad('Password too short')
                    fields['password_hash']=hash_pass(str(password))
                c.execute('UPDATE users SET '+','.join(k+'=?' for k in fields)+' WHERE hotel_id=? AND id=?',(*fields.values(),hid,id))
                if not active:c.execute('DELETE FROM sessions WHERE user_id=?',(id,))
                audit(c,hid,u['id'],'update','staff',id)
                return self.ok({'ok':True})
            if resource=='stays':
                u=self.require(c,'guests',hid,write=True)
                if not c.execute('SELECT 1 FROM stays WHERE hotel_id=? AND id=?',(hid,id)).fetchone():raise Bad('Stay not found',404)
                c.execute('UPDATE stays SET revoked=1 WHERE hotel_id=? AND id=?',(hid,id))
                audit(c,hid,u['id'],'revoke','stay',id)
                return self.ok({'ok':True})
            raise Bad('Not found',404)
    def delete(self,path):
        mh=re.fullmatch(r'/api/h/([a-z0-9-]+)/manage/([a-z_]+)/(\d+)',path)
        if not mh:raise Bad('Not found',404)
        slug,resource,id=mh.group(1),mh.group(2),int(mh.group(3))
        if resource not in FIELDS:raise Bad('Unsupported delete',404)
        with conn() as c:
            h=self.hotel(c,slug);hid=h['id'];u=self.require(c,RESOURCE_PERM[resource],hid,write=True)
            n=c.execute('UPDATE '+resource+' SET active=0 WHERE hotel_id=? AND id=?',(hid,id)).rowcount
            if not n:raise Bad('Item not found',404)
            audit(c,hid,u['id'],'archive',resource,id)
            return self.ok({'ok':True})
    def upload(self,path):
        mh=re.fullmatch(r'/api/h/([a-z0-9-]+)/manage/media',path)
        if not mh:raise Bad('Not found',404)
        with conn() as c:
            h=self.hotel(c,mh.group(1));u=self.require(c,'media',h['id'],write=True)
            data=self.raw()
            try:
                with Image.open(io.BytesIO(data)) as src:
                    if src.format not in ('JPEG','PNG','WEBP'):raise Bad('Only JPG, PNG, WebP supported')
                    if src.width*src.height>18000000:raise Bad('Image is too large')
                    im=ImageOps.exif_transpose(src);im.thumbnail((1920,1440));im=im.convert('RGB')
                    out=io.BytesIO();im.save(out,format='WEBP',quality=84,method=6)
            except (UnidentifiedImageError,OSError):raise Bad('Invalid image file')
            folder=MEDIA/str(h['id']);folder.mkdir(parents=True,exist_ok=True)
            name=secrets.token_hex(12)+'.webp';(folder/name).write_bytes(out.getvalue())
            audit(c,h['id'],u['id'],'upload','media',name)
            return self.ok({'url':'/media/'+str(h['id'])+'/'+name},201)
    def run(self,method):
        try:
            path=urlparse(self.path).path;q=parse_qs(urlparse(self.path).query)
            if method=='GET':return self.get(path,q)
            self.csrf()
            if method=='POST' and path.endswith('/manage/media'):return self.upload(path)
            if method=='DELETE':return self.delete(path)
            data=self.body()
            if method=='POST':return self.post(path,q,data)
            if method=='PUT':return self.update(path,data)
            raise Bad('Unsupported request',405)
        except Bad as e:return self.err(e.msg,e.status)
        except sqlite3.IntegrityError as e:return self.err('Duplicate or referenced item; check hotel data',409)
        except (ValueError,TypeError,OverflowError) as e:return self.err('Invalid value',400)
        except BrokenPipeError:return
        except Exception as e:
            print('Error',repr(e),flush=True)
            return self.err('Internal server error',500)
    def do_GET(self):self.run('GET')
    def do_POST(self):self.run('POST')
    def do_PUT(self):self.run('PUT')
    def do_DELETE(self):self.run('DELETE')

FAILURES={};FAILED_LOCK=threading.Lock()
if __name__=='__main__':
    init_db()
    print('STAYOS v3 listening on',PORT,'database',DB,flush=True)
    ThreadingHTTPServer(('0.0.0.0',PORT),Handler).serve_forever()
