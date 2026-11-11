#!/usr/bin/env python3
"""STAYOS MVP demonstration. NOT production authenticated."""
import json, os, secrets, sqlite3, io, re, socket, hmac, threading, time
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path
import qrcode
from PIL import Image, UnidentifiedImageError

ROOT=Path(__file__).resolve().parent
DB=Path(os.environ.get('STAYOS_DB', str(ROOT/'stay.db')))
UPLOAD_DIR=Path(os.environ.get('STAYOS_UPLOAD_DIR', str(ROOT/'static'/'assets')))
PORT=int(os.environ.get('PORT','8765'))
ADMIN_PASSWORD=os.environ.get('STAYOS_ADMIN_PASSWORD','stayos-demo')
if os.environ.get('RAILWAY_ENVIRONMENT') and ('STAYOS_ADMIN_PASSWORD' not in os.environ or ADMIN_PASSWORD=='stayos-demo'):
    raise RuntimeError('Set a strong STAYOS_ADMIN_PASSWORD in Railway Variables before deployment')
STAFF_SESSIONS={}
SESS_LOCK=threading.Lock()
LOGIN_FAILURES={}


def lan_ip():
    """Best-effort LAN IPv4 for local demonstrations (may need override on VPNs)."""
    try:
        sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        try:
            sock.connect(('192.0.2.1',80))
            ip=sock.getsockname()[0]
            if ip and not ip.startswith('127.') and not ip.startswith('169.254.'):
                return ip
        finally: sock.close()
    except OSError:
        pass
    return None

def guest_base_url():
    override=os.environ.get('STAYOS_GUEST_BASE_URL',os.environ.get('PUBLIC_BASE_URL','')).strip().rstrip('/')
    if override:
        parsed=urlparse(override)
        if parsed.scheme not in ('http','https') or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError('STAYOS_GUEST_BASE_URL must be a valid http(s) base URL')
        return override
    ip=lan_ip()
    return f'http://{ip}:{PORT}' if ip else None

def conn():
    c=sqlite3.connect(DB, timeout=15)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    return c

def initialize():
    with conn() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS hotel_settings (id INTEGER PRIMARY KEY CHECK(id=1), hotel_name TEXT NOT NULL, theme TEXT NOT NULL, welcome TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS stays (id INTEGER PRIMARY KEY, room TEXT NOT NULL, guests INTEGER NOT NULL, start TEXT NOT NULL, ends TEXT NOT NULL, zones TEXT NOT NULL, token TEXT UNIQUE NOT NULL, revoked INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS requests (id INTEGER PRIMARY KEY, stay_id INTEGER NOT NULL, kind TEXT NOT NULL, item TEXT NOT NULL, qty INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'new', created TEXT NOT NULL, FOREIGN KEY(stay_id) REFERENCES stays(id));
        CREATE TABLE IF NOT EXISTS activities (id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL, starts TEXT NOT NULL, ends TEXT NOT NULL, location TEXT NOT NULL, capacity INTEGER NOT NULL, price REAL NOT NULL, image TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS room_types (id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL, size TEXT NOT NULL, max_guests INTEGER NOT NULL, image TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS hotel_rooms (id INTEGER PRIMARY KEY, room TEXT NOT NULL UNIQUE, room_type_id INTEGER NOT NULL REFERENCES room_types(id), floor TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS amenities (id TEXT PRIMARY KEY, name TEXT NOT NULL, name_he TEXT NOT NULL, basis TEXT NOT NULL, period TEXT NOT NULL, image TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS demo_seed_state (id INTEGER PRIMARY KEY CHECK(id=1));
        CREATE TABLE IF NOT EXISTS bookings (id INTEGER PRIMARY KEY, stay_id INTEGER NOT NULL, activity_id INTEGER NOT NULL, qty INTEGER NOT NULL, created TEXT NOT NULL, UNIQUE(stay_id, activity_id));
        CREATE TABLE IF NOT EXISTS chat_messages (id INTEGER PRIMARY KEY, stay_id INTEGER NOT NULL, sender TEXT NOT NULL CHECK(sender IN ('guest','reception')), body TEXT NOT NULL, created TEXT NOT NULL, FOREIGN KEY(stay_id) REFERENCES stays(id));
        CREATE TABLE IF NOT EXISTS menu_products (id INTEGER PRIMARY KEY, name TEXT NOT NULL, name_he TEXT NOT NULL, description TEXT NOT NULL, description_he TEXT NOT NULL, category TEXT NOT NULL, price_cents INTEGER NOT NULL, image TEXT NOT NULL, available INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS food_orders (id INTEGER PRIMARY KEY, stay_id INTEGER NOT NULL REFERENCES stays(id), delivery_area TEXT NOT NULL, delivery_detail TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new', note TEXT NOT NULL DEFAULT '', total_cents INTEGER NOT NULL, created TEXT NOT NULL, request_key TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS food_order_lines (id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES food_orders(id), product_id INTEGER NOT NULL REFERENCES menu_products(id), product_name TEXT NOT NULL, qty INTEGER NOT NULL, unit_cents INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS folio_charges (id INTEGER PRIMARY KEY, stay_id INTEGER NOT NULL REFERENCES stays(id), source TEXT NOT NULL, reference_id INTEGER NOT NULL, description TEXT NOT NULL, amount_cents INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'open', created TEXT NOT NULL, settled_at TEXT, UNIQUE(source,reference_id));
        CREATE INDEX IF NOT EXISTS idx_folio_stay ON folio_charges(stay_id,status);

        CREATE INDEX IF NOT EXISTS idx_chat_stay_id ON chat_messages(stay_id,id);
        ''')
        c.execute("INSERT OR IGNORE INTO hotel_settings(id,hotel_name,theme,welcome) VALUES(1,'AZURA RESORT','luxury','Welcome to your stay')")
        existing=[x[1] for x in c.execute('PRAGMA table_info(stays)').fetchall()]
        if 'room_type_id' not in existing: c.execute('ALTER TABLE stays ADD COLUMN room_type_id INTEGER DEFAULT 1')
        if 'request_key' not in [x[1] for x in c.execute('PRAGMA table_info(food_orders)')]:
            c.execute('ALTER TABLE food_orders ADD COLUMN request_key TEXT')
        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_food_order_key ON food_orders(request_key)')
        if not c.execute('SELECT 1 FROM menu_products LIMIT 1').fetchone():
            c.executemany('INSERT INTO menu_products(id,name,name_he,description,description_he,category,price_cents,image) VALUES(?,?,?,?,?,?,?,?)',[
                (1,'Fresh Coconut','קוקוס טרי','Chilled young coconut','קוקוס צעיר מצונן','drinks',650,'coconut.webp'),
                (2,'Tropical Smoothie','שייק טרופי','Mango and pineapple smoothie','שייק מנגו ואננס','drinks',850,'smoothie.webp'),
                (3,'Iced Ceylon Tea','תה ציילון קר','Refreshing local tea with lime','תה ציילון מקומי עם ליים','drinks',450,'tea.webp'),
                (4,'Club Sandwich','קלאב סנדוויץ׳','Toasted sandwich with fries','כריך קלוי עם צ׳יפס','food',1350,'sandwich.webp'),
                (5,'Tropical Fruit Plate','מגש פירות טרופיים','Seasonal fruit selection','מבחר פירות העונה','food',990,'fruit.webp'),
                (6,'Sri Lankan Vegetable Curry','קארי ירקות סרי לנקי','Coconut rice and vegetables','אורז קוקוס וירקות','food',1600,'curry.webp'),
                (7,'Poolside French Fries','צ׳יפס לבריכה','Crispy golden fries','צ׳יפס פריך וזהוב','snacks',700,'fries.webp'),
                (8,'Chocolate Brownie','בראוני שוקולד','Warm brownie dessert','קינוח בראוני חמים','snacks',800,'brownie.webp')])
        if c.execute('SELECT COUNT(*) FROM room_types').fetchone()[0]==0:
            c.executemany('INSERT INTO room_types(id,name,description,size,max_guests,image) VALUES(?,?,?,?,?,?)',[
                (1,'Deluxe Garden Room','A serene retreat with a king bed, private terrace and garden views.','35 m²',3,'room-garden.webp'),
                (2,'Ocean View Suite','Spacious suite with a lounge area, balcony and breathtaking ocean views.','58 m²',4,'room-ocean.webp'),
                (3,'Family Pool Villa','Two-bedroom villa with a private plunge pool and family-friendly living space.','92 m²',5,'room-villa.webp')])
        seed_activities=c.execute('SELECT 1 FROM demo_seed_state WHERE id=1').fetchone() is None
        if seed_activities and not c.execute('SELECT 1 FROM amenities LIMIT 1').fetchone():
            c.executemany('INSERT INTO amenities(id,name,name_he,basis,period,image) VALUES(?,?,?,?,?,?)',[
                ('towels','Fresh Towels','מגבות נקיות','guest','daily','towels.svg'),('water','Water Bottles','בקבוקי מים','guest','daily','water.svg'),
                ('toothbrush','Toothbrushes','מברשות שיניים','guest','stay','toothbrush.svg'),('slippers','Slippers','נעלי בית','guest','stay','slippers.svg'),
                ('soap','Body Wash','סבון גוף','room','daily','soap.svg'),('paper','Toilet Paper','נייר טואלט','room','daily','paper.svg')])
        c.execute('INSERT OR IGNORE INTO demo_seed_state(id) VALUES(1)')
        c.execute('''INSERT OR IGNORE INTO hotel_rooms(room,room_type_id,floor)
          SELECT DISTINCT s.room, CASE WHEN EXISTS(SELECT 1 FROM room_types t WHERE t.id=s.room_type_id) THEN s.room_type_id ELSE 1 END, '' FROM stays s''')
        if seed_activities and not c.execute('SELECT 1 FROM hotel_rooms LIMIT 1').fetchone():
            c.executemany('INSERT INTO hotel_rooms(room,room_type_id,floor) VALUES(?,?,?)', [('101',1,'1'),('204',1,'2'),('305',2,'3'),('401',3,'4')])
        if seed_activities and c.execute('SELECT COUNT(*) FROM activities').fetchone()[0]==0:
            from datetime import timedelta
            now=datetime.now().astimezone()
            for delta,hour,title,desc,loc,capacity,price,image in [
                (0,8,'Sunrise Yoga','Start the day by the ocean','Beach Deck',12,0,'yoga'),
                (0,11,'Sri Lankan Cooking Class','Learn to prepare local dishes','Main Restaurant',8,20,'cooking'),
                (0,19,'Live Music','Acoustic evening under the stars','Pool Bar',40,0,'music'),
                (1,9,'Surf Lesson','Learn to ride your first wave','Beach',6,15,'surf'),
                (1,16,'Ceylon Tea Tasting','Discover local tea traditions','Lounge',10,0,'tea'),
                (2,8,'Morning Meditation','A peaceful start to the day','Yoga Pavilion',12,0,'yoga'),
                (2,14,'Local Craft Workshop','Creative afternoon with local artists','Activity Room',8,10,'craft'),
                (3,18,'Garden Dinner','An evening among palm trees','Garden',30,25,'cooking'),
            ]:
                d=(now+timedelta(days=delta)).replace(hour=hour,minute=0,second=0,microsecond=0)
                end=d+timedelta(minutes=90)
                c.execute('INSERT INTO activities(title,description,starts,ends,location,capacity,price,image) VALUES(?,?,?,?,?,?,?,?)',(title,desc,d.isoformat(),end.isoformat(),loc,capacity,price,image))

def replenish_demo_activities():
    """Keep the example resort's activity calendar populated after a week of demos."""
    from datetime import timedelta
    now=datetime.now().astimezone()
    with conn() as c:
        future=c.execute('SELECT COUNT(*) FROM activities WHERE starts>?',(now.isoformat(),)).fetchone()[0]
        if future:return
        recipes=[(8,'Sunrise Yoga','Oceanfront sunrise wellness session','Beach Deck',12,0,'yoga'),(11,'Sri Lankan Cooking Class','A hands-on introduction to local flavours','Main Restaurant',8,20,'cooking'),(17,'Sunset Yoga','Wind down beside the Indian Ocean','Ocean Deck',12,0,'yoga'),(19,'Live Music','Evening acoustic sounds by the pool','Pool Bar',40,0,'music')]
        for day in range(1,8):
            for hour,title,desc,loc,cap,price,img in recipes:
                d=(now+timedelta(days=day)).replace(hour=hour,minute=0,second=0,microsecond=0)
                c.execute('INSERT INTO activities(title,description,starts,ends,location,capacity,price,image) VALUES(?,?,?,?,?,?,?,?)',(title,desc,d.isoformat(),(d+timedelta(minutes=60)).isoformat(),loc,cap,price,img))

def now_iso(): return datetime.now().astimezone().isoformat()
def iso(x):
    try:
        v=datetime.fromisoformat(x)
        if v.tzinfo is None: raise ValueError()
        return v
    except (ValueError,TypeError): raise ValueError('Date/time must be ISO8601 with timezone offset')

def stay_valid(s):
    now=datetime.now(timezone.utc)
    return bool(not s['revoked'] and iso(s['start'])<=now<=iso(s['ends']))

def stay_info(s):
    return {'room':s['room'],'guests':s['guests'],'start':s['start'],'ends':s['ends'],'zones':json.loads(s['zones']),'room_type_id':s['room_type_id'],'active':stay_valid(s)}

def getstay(c,token):
    row=c.execute('SELECT * FROM stays WHERE token=?',(token,)).fetchone()
    if not row: raise ValueError('This guest pass does not exist')
    if not stay_valid(row): raise ValueError('This guest pass has expired, is not yet active, or was revoked')
    return row

LIMITS={'towels':('Fresh Towels','guest','daily'),'water':('Water Bottles','guest','daily'),'toothbrush':('Toothbrushes','guest','stay'),'slippers':('Slippers','guest','stay'),'soap':('Body Wash','room','daily'),'paper':('Toilet Paper','room','daily')}

class Handler(BaseHTTPRequestHandler):
    def staff_authed(self):
        import http.cookies
        cookie=http.cookies.SimpleCookie()
        try:cookie.load(self.headers.get('Cookie',''))
        except Exception:return False
        morsel=cookie.get('stayos_staff')
        if not morsel:return False
        key=morsel.value
        with SESS_LOCK:
            expiry=STAFF_SESSIONS.get(key,0)
            if expiry<time.time():
                STAFF_SESSIONS.pop(key,None);return False
            return True
    def staff_required(self):
        if not self.staff_authed():
            self.fail('Staff login required',401)
            return False
        return True
    def login(self,password):
        ip=self.client_address[0];now=time.monotonic()
        with SESS_LOCK:
            failures=[v for v in LOGIN_FAILURES.get(ip,[]) if v>now-300]
            if len(failures)>=8:
                self.fail('Too many attempts. Try again in 5 minutes.',429);return
            if not hmac.compare_digest(str(password),ADMIN_PASSWORD):
                failures.append(now);LOGIN_FAILURES[ip]=failures
                self.fail('Invalid staff password',401);return
            LOGIN_FAILURES.pop(ip,None)
            token=secrets.token_urlsafe(32)
            STAFF_SESSIONS[token]=time.time()+12*3600
        content=b'{"ok":true}'
        self.send_response(200);self.send_header('Content-Type','application/json; charset=utf-8')
        cookie=f'stayos_staff={token}; HttpOnly; Path=/; SameSite=Strict; Max-Age=43200'
        if os.environ.get('STAYOS_COOKIE_SECURE')=='1':cookie+='; Secure'
        self.send_header('Set-Cookie',cookie)
        self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)
    def log_message(self,*args): pass
    def respond(self,obj,status=200):
        buf=json.dumps(obj,ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(buf))); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(buf)
    def fail(self,msg,status=400): self.respond({'error':str(msg)},status)
    def body(self):
        n=int(self.headers.get('Content-Length','0'))
        if n>32768: raise ValueError('Request too large')
        return json.loads(self.rfile.read(n) or b'{}')
    def do_GET(self):
        try:
            u=urlparse(self.path); path=u.path; q=parse_qs(u.query)
            if path=='/api/auth/me':
                self.respond({'authenticated':self.staff_authed()});return
            if path in ('/api/stays','/api/requests','/api/admin-bookings','/api/chat/threads','/api/chat/admin','/api/orders/admin','/api/folio/admin','/api/hotel-rooms','/api/menu/admin','/api/amenities/admin') and not self.staff_required(): return
            if path=='/api/guest-manifest':
                # Give the installed PWA a stay-specific start URL, preserving the room on relaunch.
                token=q.get('token',[''])[0]
                with conn() as c:
                    getstay(c,token)
                    hotel_name=c.execute('SELECT hotel_name FROM hotel_settings WHERE id=1').fetchone()['hotel_name']
                manifest={'name':hotel_name+' Guest Services','short_name':hotel_name[:12],
                  'id':'/guest-app/'+token[:16],
                  'start_url':'/guest?guest='+token,'scope':'/','display':'standalone',
                  'theme_color':'#173e35','background_color':'#f8faf7',
                  'icons':[{'src':'/assets/app-icon-192.png','sizes':'192x192','type':'image/png','purpose':'any maskable'},{'src':'/assets/app-icon-512.png','sizes':'512x512','type':'image/png','purpose':'any maskable'}]}
                content=json.dumps(manifest).encode('utf-8')
                self.send_response(200);self.send_header('Content-Type','application/manifest+json; charset=utf-8')
                self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(content)))
                self.end_headers();self.wfile.write(content);return
            if path=='/api/settings':
                with conn() as c: self.respond(dict(c.execute('SELECT hotel_name,theme,welcome FROM hotel_settings WHERE id=1').fetchone()));return
            if path=='/api/network':
                base=guest_base_url()
                self.respond({'guest_base_url':base,'is_configured':bool(os.environ.get('STAYOS_GUEST_BASE_URL') or os.environ.get('PUBLIC_BASE_URL')),'warning':None if base else 'No LAN address detected. Set STAYOS_GUEST_BASE_URL before generating QR.'});return
            if path=='/api/stays':
                with conn() as c:
                    self.respond([dict(x)|{'zones':json.loads(x['zones']),'active':stay_valid(x)} for x in c.execute('SELECT id,room,guests,start,ends,zones,token,revoked,created FROM stays ORDER BY id DESC')]);return
            if path=='/api/resort-info':
                self.respond({'name':'AZURA RESORT','location':'Sri Lanka · Indian Ocean','description':'A private sanctuary where tropical design, exceptional service and ocean tranquility meet. Discover signature rooms, curated dining and wellbeing rituals.','hero':'resort-hero.webp','facilities':['Infinity pool','Beachfront dining','Wellness spa','Private transfers','Daily activities','24-hour reception'],'checkin':'14:00','checkout':'11:00'});return
            if path=='/api/hotel-rooms':
                with conn() as c: self.respond([dict(x) for x in c.execute('SELECT h.*,t.name AS room_type_name FROM hotel_rooms h JOIN room_types t ON t.id=h.room_type_id ORDER BY h.room')]);return
            if path=='/api/amenities/admin':
                with conn() as c:self.respond([dict(x) for x in c.execute('SELECT * FROM amenities ORDER BY id')]);return
            if path=='/api/amenities':
                with conn() as c:self.respond([dict(x) for x in c.execute('SELECT * FROM amenities WHERE enabled=1 ORDER BY id')]);return
            if path=='/api/menu/admin':
                with conn() as c: self.respond([dict(x) for x in c.execute('SELECT * FROM menu_products ORDER BY category,id')]);return
            if path=='/api/room-types':
                with conn() as c:self.respond([dict(x) for x in c.execute('SELECT * FROM room_types ORDER BY id')]);return
            if path=='/api/activities':
                with conn() as c:
                    rows=c.execute('SELECT a.*, COALESCE(SUM(b.qty),0) AS booked FROM activities a LEFT JOIN bookings b ON b.activity_id=a.id GROUP BY a.id ORDER BY starts').fetchall()
                    self.respond([dict(x) for x in rows]);return
            if path=='/api/admin-bookings':
                with conn() as c:
                    rows=c.execute('SELECT b.*, s.room, a.title, a.starts FROM bookings b JOIN stays s ON b.stay_id=s.id JOIN activities a ON a.id=b.activity_id ORDER BY b.id DESC').fetchall()
                    self.respond([dict(x) for x in rows]);return
            if path=='/api/chat/guest':
                with conn() as c:
                    stay=getstay(c,q.get('token',[''])[0])
                    rows=c.execute('SELECT id,sender,body,created FROM chat_messages WHERE stay_id=? ORDER BY id ASC LIMIT 500',(stay['id'],)).fetchall()
                    self.respond({'room':stay['room'],'messages':[dict(x) for x in rows]});return
            if path=='/api/chat/threads':
                with conn() as c:
                    rows=c.execute('''SELECT s.id AS stay_id,s.room,s.guests,s.ends,s.revoked,
                          (SELECT body FROM chat_messages m WHERE m.stay_id=s.id ORDER BY id DESC LIMIT 1) AS latest,
                          (SELECT created FROM chat_messages m WHERE m.stay_id=s.id ORDER BY id DESC LIMIT 1) AS last_at,
                          (SELECT COUNT(*) FROM chat_messages m WHERE m.stay_id=s.id AND sender='guest') AS guest_count,
                          (SELECT COALESCE(MAX(id),0) FROM chat_messages m WHERE m.stay_id=s.id AND sender='guest') AS latest_guest_id
                          FROM stays s WHERE EXISTS(SELECT 1 FROM chat_messages m WHERE m.stay_id=s.id)
                          ORDER BY last_at DESC''').fetchall()
                    self.respond([dict(x) for x in rows]);return
            if path=='/api/chat/admin':
                stay_id=int(q.get('stay_id',['0'])[0]);
                with conn() as c:
                    stay=c.execute('SELECT id,room FROM stays WHERE id=?',(stay_id,)).fetchone()
                    if not stay: raise ValueError('Room conversation not found')
                    rows=c.execute('SELECT id,sender,body,created FROM chat_messages WHERE stay_id=? ORDER BY id ASC LIMIT 500',(stay_id,)).fetchall()
                    self.respond({'room':stay['room'],'messages':[dict(x) for x in rows]});return
            if path=='/api/requests':
                with conn() as c:
                    rows=c.execute('SELECT r.*, s.room FROM requests r JOIN stays s ON r.stay_id=s.id ORDER BY r.id DESC').fetchall()
                    self.respond([dict(x) for x in rows]);return
            if path=='/api/menu':
                with conn() as c:self.respond([dict(x) for x in c.execute('SELECT * FROM menu_products WHERE available=1 ORDER BY category,id')]);return
            if path=='/api/orders/guest':
                with conn() as c:
                    stay=getstay(c,q.get('token',[''])[0]);rows=c.execute('SELECT * FROM food_orders WHERE stay_id=? ORDER BY id DESC',(stay['id'],)).fetchall()
                    self.respond([dict(x)|{'lines':[dict(z) for z in c.execute('SELECT product_name,qty,unit_cents FROM food_order_lines WHERE order_id=?',(x['id'],))]} for x in rows]);return
            if path=='/api/orders/admin':
                with conn() as c:
                    rows=c.execute('SELECT o.*,s.room FROM food_orders o JOIN stays s ON s.id=o.stay_id ORDER BY o.id DESC').fetchall()
                    self.respond([dict(x)|{'lines':[dict(z) for z in c.execute('SELECT product_name,qty,unit_cents FROM food_order_lines WHERE order_id=?',(x['id'],))]} for x in rows]);return
            if path=='/api/folio/admin':
                with conn() as c:
                    rows=c.execute('SELECT f.*,s.room FROM folio_charges f JOIN stays s ON s.id=f.stay_id ORDER BY f.id DESC').fetchall()
                    self.respond([dict(x) for x in rows]);return
            if path=='/api/guest':
                with conn() as c:
                    s=getstay(c,q.get('token',[''])[0]);self.respond(stay_info(s));return
            if path=='/api/my':
                with conn() as c:
                    s=getstay(c,q.get('token',[''])[0]); self.respond({'requests':[dict(x) for x in c.execute('SELECT * FROM requests WHERE stay_id=? ORDER BY id DESC',(s['id'],))], 'bookings':[dict(x) for x in c.execute('SELECT b.*, a.title,a.starts FROM bookings b JOIN activities a ON b.activity_id=a.id WHERE stay_id=?',(s['id'],))], 'folio':[dict(x) for x in c.execute('SELECT id,description,amount_cents,status,created FROM folio_charges WHERE stay_id=? ORDER BY id DESC',(s['id'],))]});return
            if path=='/api/qr':
                token=q.get('token',[''])[0]
                with conn() as c:
                    if not c.execute('SELECT 1 FROM stays WHERE token=?',(token,)).fetchone(): return self.fail('Unknown token',404)
                base=guest_base_url()
                if not base: return self.fail('No LAN IP detected. Set STAYOS_GUEST_BASE_URL to your computer LAN address.',503)
                url=f'{base}/guest?guest={token}'
                qr=qrcode.QRCode(box_size=7,border=3,error_correction=qrcode.constants.ERROR_CORRECT_M)
                qr.add_data(url); qr.make(fit=True); image=qr.make_image(fill_color='#163e35',back_color='white')
                out=io.BytesIO();image.save(out,format='PNG');buf=out.getvalue()
                self.send_response(200);self.send_header('Content-Type','image/png');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(buf)));self.end_headers();self.wfile.write(buf);return
            if path.startswith('/assets/hotel-') and path.endswith('.webp'):
                name=path.split('/')[-1]
                if re.fullmatch(r'hotel-[a-f0-9]{24}\.webp',name):
                    p=UPLOAD_DIR/name
                    if p.is_file():
                        content=p.read_bytes();self.send_response(200);self.send_header('Content-Type','image/webp');self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content);return
            f=ROOT/'static'/('index.html' if path in ('/','/index.html','/guest') else path.lstrip('/'))
            if f.is_file() and f.resolve().is_relative_to((ROOT/'static').resolve()):
                content=f.read_bytes(); ext=f.suffix
                mime={'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'application/javascript; charset=utf-8','.svg':'image/svg+xml','.json':'application/manifest+json','.png':'image/png','.webp':'image/webp'}.get(ext,'application/octet-stream')
                self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Cache-Control','no-store' if ext in ('.html','.js','.json') else 'public, max-age=300');self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content);return
            self.fail('Not found',404)
        except ValueError as e: self.fail(e)
        except Exception as e: self.fail(str(e),500)
    def do_POST(self):
        try:
            path=urlparse(self.path).path
            if path=='/api/upload-asset':
                if not self.staff_required():return
                if self.headers.get('Content-Type','').split(';')[0] not in ('image/jpeg','image/png','image/webp'):raise ValueError('Upload a JPG, PNG or WebP image')
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=4*1024*1024:raise ValueError('Image size must be under 4MB')
                data=self.rfile.read(size)
                try:
                    pic=Image.open(io.BytesIO(data));pic.verify()
                    pic=Image.open(io.BytesIO(data))
                    if pic.width*pic.height>25000000:raise ValueError('Image dimensions are too large')
                    pic.thumbnail((1800,1800));pic=pic.convert('RGB')
                except (UnidentifiedImageError,OSError):raise ValueError('Invalid image file')
                name='hotel-'+secrets.token_hex(12)+'.webp'
                asset_dir=UPLOAD_DIR;asset_dir.mkdir(parents=True,exist_ok=True)
                pic.save(asset_dir/name,format='WEBP',quality=83)
                self.respond({'ok':True,'image':name});return
            b=self.body()
            if path=='/api/auth/login': self.login(b.get('password',''));return
            if path=='/api/auth/logout':
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Set-Cookie','stayos_staff=; HttpOnly; Path=/; SameSite=Strict; Max-Age=0');self.send_header('Content-Length','11');self.end_headers();self.wfile.write(b'{"ok":true}');return
            if path in ('/api/settings','/api/stays','/api/chat/admin','/api/status','/api/revoke','/api/orders/status','/api/folio/settle','/api/catalog') and not self.staff_required():return

            if path=='/api/catalog':
                section=str(b.get('section',''))
                action=str(b.get('action','save'))
                record=b.get('record',{})
                if section not in ('hotel_rooms','room_types','menu_products','activities','amenities') or action not in ('save','delete') or not isinstance(record,dict):raise ValueError('Invalid catalog request')
                id=(str(record.get('id') or '') if section=='amenities' else int(record.get('id') or 0))
                def field(key,limit=140,required=True):
                    v=str(record.get(key,'')).strip()
                    if len(v)>limit or (required and not v):raise ValueError('Invalid '+key)
                    return v
                def imagefield(default):
                    v=str(record.get('image') or default)
                    if not re.fullmatch(r'[A-Za-z0-9_-]+(?:\.(?:webp|png|svg))?',v) or '..' in v:raise ValueError('Invalid image')
                    if not (ROOT/'static'/'assets'/ (v if '.' in v else v+'.svg')).is_file():raise ValueError('Choose an existing demo asset')
                    return v
                with conn() as c:
                    c.execute('BEGIN IMMEDIATE')
                    if action=='delete':
                        if not id:raise ValueError('Select an item')
                        if section=='hotel_rooms':
                            row=c.execute('SELECT room FROM hotel_rooms WHERE id=?',(id,)).fetchone()
                            if not row:raise ValueError('Room not found')
                            if c.execute('SELECT 1 FROM stays WHERE room=?',(row['room'],)).fetchone():raise ValueError('Room has stay history; retain it for audit')
                        elif section=='room_types' and (c.execute('SELECT 1 FROM hotel_rooms WHERE room_type_id=?',(id,)).fetchone() or c.execute('SELECT 1 FROM stays WHERE room_type_id=?',(id,)).fetchone()):raise ValueError('Room type is in use')
                        elif section=='activities' and c.execute('SELECT 1 FROM bookings WHERE activity_id=?',(id,)).fetchone():raise ValueError('Activity has bookings; keep it for audit')
                        elif section=='amenities' and c.execute('SELECT 1 FROM requests WHERE item=?',(str(record.get('id')),)).fetchone():
                            c.execute('UPDATE amenities SET enabled=0 WHERE id=?',(str(record.get('id')),));c.commit();self.respond({'ok':True,'archived':True});return
                        elif section=='menu_products' and c.execute('SELECT 1 FROM food_order_lines WHERE product_id=?',(id,)).fetchone():
                            c.execute('UPDATE menu_products SET available=0 WHERE id=?',(id,));c.commit();self.respond({'ok':True,'archived':True});return
                        c.execute('DELETE FROM '+section+' WHERE id=?',((str(record.get('id')) if section=='amenities' else id),))
                        c.commit();self.respond({'ok':True});return
                    if section=='amenities' and action=='save':
                        slug=field('id',36) if id else field('slug',36)
                        if not re.fullmatch(r'[a-z][a-z0-9_-]{1,35}',slug):raise ValueError('Use a short lowercase item code')
                        basis=field('basis',10);period=field('period',10)
                        if basis not in ('guest','room') or period not in ('daily','stay'):raise ValueError('Invalid request limit')
                        values=(field('name',90),field('name_he',90),basis,period,imagefield('towels.svg'),int(bool(record.get('enabled',1))))
                        if id:
                            if not c.execute('SELECT 1 FROM amenities WHERE id=?',(id,)).fetchone():raise ValueError('Amenity not found')
                            c.execute('UPDATE amenities SET name=?,name_he=?,basis=?,period=?,image=?,enabled=? WHERE id=?',(*values,id))
                        else:
                            c.execute('INSERT INTO amenities(id,name,name_he,basis,period,image,enabled) VALUES(?,?,?,?,?,?,?)',(slug,*values))
                        c.commit();self.respond({'ok':True,'id':slug});return
                    if section=='hotel_rooms':
                        room=field('room',16);rtype=int(record.get('room_type_id',0));floor=field('floor',24,False);notes=field('notes',200,False)
                        if not re.fullmatch(r'[A-Za-z0-9-]{1,16}',room):raise ValueError('Invalid room number')
                        if not c.execute('SELECT 1 FROM room_types WHERE id=?',(rtype,)).fetchone():raise ValueError('Choose a room type')
                        values=(room,rtype,floor,notes)
                        if id:
                            prior=c.execute('SELECT room FROM hotel_rooms WHERE id=?',(id,)).fetchone()
                            if not prior:raise ValueError('Room not found')
                            if prior['room']!=room and c.execute('SELECT 1 FROM stays WHERE room=?',(prior['room'],)).fetchone():raise ValueError('Cannot rename a room with stay history')
                            c.execute('UPDATE hotel_rooms SET room=?,room_type_id=?,floor=?,notes=? WHERE id=?',(*values,id))
                        else:id=c.execute('INSERT INTO hotel_rooms(room,room_type_id,floor,notes) VALUES(?,?,?,?)',values).lastrowid
                    elif section=='room_types':
                        values=(field('name',90),field('description',800),field('size',35),int(record.get('max_guests',0)),imagefield('room-garden.webp'))
                        if not 1<=values[3]<=20:raise ValueError('Invalid max guests')
                        if id:c.execute('UPDATE room_types SET name=?,description=?,size=?,max_guests=?,image=? WHERE id=?',(*values,id))
                        else:id=c.execute('INSERT INTO room_types(name,description,size,max_guests,image) VALUES(?,?,?,?,?)',values).lastrowid
                    elif section=='menu_products':
                        category=field('category',20)
                        if category not in ('drinks','food','snacks'):raise ValueError('Invalid menu category')
                        price=int(record.get('price_cents',-1));available=int(bool(record.get('available',1)))
                        if not 0<=price<=1000000:raise ValueError('Invalid price')
                        values=(field('name',100),field('name_he',100),field('description',400),field('description_he',400),category,price,imagefield('coconut.webp'),available)
                        if id:c.execute('UPDATE menu_products SET name=?,name_he=?,description=?,description_he=?,category=?,price_cents=?,image=?,available=? WHERE id=?',(*values,id))
                        else:id=c.execute('INSERT INTO menu_products(name,name_he,description,description_he,category,price_cents,image,available) VALUES(?,?,?,?,?,?,?,?)',values).lastrowid
                    else:
                        starts=iso(field('starts',50));ends=iso(field('ends',50));capacity=int(record.get('capacity',0));price=float(record.get('price',-1))
                        if ends<=starts or not 1<=capacity<=10000 or not 0<=price<=100000:raise ValueError('Invalid schedule, capacity or price')
                        booked=c.execute('SELECT COALESCE(SUM(qty),0) FROM bookings WHERE activity_id=?',(id,)).fetchone()[0] if id else 0
                        if capacity<booked:raise ValueError('Capacity below current bookings')
                        values=(field('title',120),field('description',800),starts.isoformat(),ends.isoformat(),field('location',100),capacity,price,imagefield('yoga'))
                        if id:c.execute('UPDATE activities SET title=?,description=?,starts=?,ends=?,location=?,capacity=?,price=?,image=? WHERE id=?',(*values,id))
                        else:id=c.execute('INSERT INTO activities(title,description,starts,ends,location,capacity,price,image) VALUES(?,?,?,?,?,?,?,?)',values).lastrowid
                self.respond({'ok':True,'id':id});return
            if path=='/api/settings':
                name=str(b.get('hotel_name','')).strip();theme=str(b.get('theme','')).strip();welcome=str(b.get('welcome','')).strip()
                if not (2<=len(name)<=80) or theme not in ('luxury','ocean','boutique') or not (2<=len(welcome)<=120): raise ValueError('Invalid hotel branding settings')
                with conn() as c: c.execute('UPDATE hotel_settings SET hotel_name=?,theme=?,welcome=? WHERE id=1',(name,theme,welcome))
                self.respond({'ok':True,'hotel_name':name,'theme':theme,'welcome':welcome});return
            if path=='/api/stays':
                room=str(b.get('room','')).strip();guests=int(b.get('guests',0));start=iso(b.get('start'));ends=iso(b.get('ends'));zones=b.get('zones',[])
                if not re.fullmatch(r'[A-Za-z0-9\-]{1,16}',room) or not 1<=guests<=12 or ends<=start or not isinstance(zones,list) or not set(zones)<=set(('room','activities','pool','vip','kids')) or not zones: raise ValueError('Invalid stay settings')
                room_type_id=int(b.get('room_type_id',1))
                with conn() as c:
                    rt=c.execute('SELECT * FROM room_types WHERE id=?',(room_type_id,)).fetchone()
                if not rt or guests>rt['max_guests']: raise ValueError('Room type does not allow this number of guests')
                with conn() as c:
                    actual=c.execute('SELECT room_type_id FROM hotel_rooms WHERE room=?',(room,)).fetchone()
                if not actual:raise ValueError('Room is not in the hotel room list. Add it in Rooms first')
                if actual['room_type_id']!=room_type_id:raise ValueError('Selected room type does not match the hotel room list')
                token=secrets.token_urlsafe(32)
                with conn() as c:
                    c.execute('UPDATE stays SET revoked=1 WHERE room=? AND revoked=0',(room,))
                    cur=c.execute('INSERT INTO stays(room,guests,start,ends,zones,token,created,room_type_id) VALUES(?,?,?,?,?,?,?,?)',(room,guests,start.isoformat(),ends.isoformat(),json.dumps(zones),token,now_iso(),room_type_id))
                self.respond({'id':cur.lastrowid,'token':token,'room':room,'url':f'/guest?guest={token}'},201);return
            if path=='/api/admin-bookings':
                with conn() as c:
                    rows=c.execute('SELECT b.*, s.room, a.title, a.starts FROM bookings b JOIN stays s ON b.stay_id=s.id JOIN activities a ON a.id=b.activity_id ORDER BY b.id DESC').fetchall()
                    self.respond([dict(x) for x in rows]);return
            if path=='/api/requests':
                with conn() as c:
                    c.execute('BEGIN IMMEDIATE');s=getstay(c,str(b.get('token','')))
                    if 'room' not in json.loads(s['zones']): raise ValueError('Room service is not enabled')
                    item=str(b.get('item',''));qty=int(b.get('qty',0));kind='cleaning' if item=='cleaning' else 'item'
                    if item=='cleaning':
                        if qty!=1:raise ValueError('Invalid cleaning request')
                        existing=c.execute("SELECT COUNT(*) FROM requests WHERE stay_id=? AND item='cleaning' AND status!='done'",(s['id'],)).fetchone()[0]
                        if existing: raise ValueError('A cleaning request is already open')
                    else:
                        setting=c.execute('SELECT basis,period FROM amenities WHERE id=? AND enabled=1',(item,)).fetchone()
                        if not setting:raise ValueError('Unknown or disabled item')
                        basis,period=setting['basis'],setting['period'];maxqty=s['guests'] if basis=='guest' else 2
                        if qty<1:raise ValueError('Select at least one item')
                        period_sql='AND date(created)=date(?)' if period=='daily' else ''
                        args=(s['id'],item,now_iso()) if period=='daily' else (s['id'],item)
                        used=c.execute(f'SELECT COALESCE(SUM(qty),0) FROM requests WHERE stay_id=? AND item=? {period_sql}',args).fetchone()[0]
                        if qty+used>maxqty:raise ValueError(f'Limit reached: {maxqty} per {period} for this room')
                    cur=c.execute('INSERT INTO requests(stay_id,kind,item,qty,created) VALUES(?,?,?,?,?)',(s['id'],kind,item,qty,now_iso()))
                    self.respond({'id':cur.lastrowid,'status':'new'},201);return
            if path=='/api/orders':
                area=str(b.get('delivery_area',''));detail=str(b.get('delivery_detail','')).strip();note=str(b.get('note','')).strip()
                if area not in ('room','pool','lobby','beach','restaurant','garden') or len(detail)>80 or len(note)>250: raise ValueError('Invalid delivery location or note')
                if area!='room' and len(detail)<2:raise ValueError('Please specify your location (table, sunbed or landmark)')
                request_key=str(b.get('request_key','')).strip()
                if not re.fullmatch(r'[A-Za-z0-9_-]{12,100}',request_key):raise ValueError('Invalid request key')
                lines=b.get('items',[])
                if not isinstance(lines,list) or not 1<=len(lines)<=16:raise ValueError('Choose menu items')
                quantities={}
                for line in lines:
                    if not isinstance(line,dict): raise ValueError('Invalid menu selection')
                    pid=int(line.get('product_id',0));qty=int(line.get('qty',0))
                    if qty<1 or qty>12 or pid in quantities:raise ValueError('Invalid item quantity')
                    quantities[pid]=qty
                with conn() as c:
                    c.execute('BEGIN IMMEDIATE');stay=getstay(c,str(b.get('token','')))
                    previous=c.execute('SELECT id,total_cents,status FROM food_orders WHERE stay_id=? AND request_key=?',(stay['id'],request_key)).fetchone()
                    if previous:
                        self.respond({'id':previous['id'],'total_cents':previous['total_cents'],'status':previous['status'],'payment':'charge_to_room','duplicate':True},200);return
                    if 'room' not in json.loads(stay['zones']):raise ValueError('Food service is not enabled for this stay')
                    dbitems=c.execute('SELECT * FROM menu_products WHERE available=1 AND id IN ('+','.join('?'*len(quantities))+')',tuple(quantities)).fetchall()
                    if len(dbitems)!=len(quantities):raise ValueError('Unknown or unavailable product')
                    total=sum(z['price_cents']*quantities[z['id']] for z in dbitems)
                    if total>150000:raise ValueError('Order exceeds demo limit')
                    cur=c.execute('INSERT INTO food_orders(stay_id,delivery_area,delivery_detail,status,note,total_cents,created,request_key) VALUES(?,?,?,?,?,?,?,?)',(stay['id'],area,detail,'new',note,total,now_iso(),request_key))
                    oid=cur.lastrowid
                    c.executemany('INSERT INTO food_order_lines(order_id,product_id,product_name,qty,unit_cents) VALUES(?,?,?,?,?)',[(oid,z['id'],z['name'],quantities[z['id']],z['price_cents']) for z in dbitems])
                    c.execute('INSERT INTO folio_charges(stay_id,source,reference_id,description,amount_cents,created) VALUES(?,?,?,?,?,?)',(stay['id'],'food',oid,f'Menu order #{oid} · {area}',total,now_iso()))
                self.respond({'id':oid,'total_cents':total,'status':'new','payment':'charge_to_room'},201);return
            if path=='/api/orders/status':
                status=str(b.get('status',''));oid=int(b.get('id',0))
                if status not in ('new','preparing','on_way','delivered','cancelled'):raise ValueError('Invalid status')
                with conn() as c:
                    c.execute('BEGIN IMMEDIATE');order=c.execute('SELECT * FROM food_orders WHERE id=?',(oid,)).fetchone()
                    if not order:raise ValueError('Order not found')
                    if order['status']=='cancelled' and status!='cancelled':raise ValueError('Cancelled order cannot be reopened')
                    if status=='cancelled' and order['status']=='delivered':raise ValueError('Delivered order cannot be cancelled; settle or adjust manually')
                    if status=='cancelled':
                        charged=c.execute("SELECT status FROM folio_charges WHERE source='food' AND reference_id=?",(oid,)).fetchone()
                        if charged and charged['status']=='settled':raise ValueError('Settled charge cannot be cancelled')
                        c.execute("UPDATE folio_charges SET status='void' WHERE source='food' AND reference_id=?",(oid,))
                    c.execute('UPDATE food_orders SET status=? WHERE id=?',(status,oid))
                self.respond({'ok':True});return
            if path=='/api/folio/settle':
                stay_id=int(b.get('stay_id',0));method=str(b.get('method',''))
                if method not in ('cash','card_terminal','other'):raise ValueError('Select payment method')
                with conn() as c:
                    c.execute('BEGIN IMMEDIATE')
                    if not c.execute('SELECT 1 FROM stays WHERE id=?',(stay_id,)).fetchone():raise ValueError('Stay not found')
                    amount=c.execute("SELECT COALESCE(SUM(amount_cents),0) FROM folio_charges WHERE stay_id=? AND status='open'",(stay_id,)).fetchone()[0]
                    c.execute("UPDATE folio_charges SET status='settled',settled_at=? WHERE stay_id=? AND status='open'",(now_iso()+' / '+method,stay_id))
                self.respond({'ok':True,'settled_cents':amount,'method':method});return
            if path=='/api/bookings':
                with conn() as c:
                    c.execute('BEGIN IMMEDIATE');s=getstay(c,str(b.get('token','')))
                    if 'activities' not in json.loads(s['zones']):raise ValueError('Activities are not enabled')
                    activity=int(b.get('activity_id',0));qty=int(b.get('qty',0))
                    a=c.execute('SELECT * FROM activities WHERE id=?',(activity,)).fetchone()
                    if not a or not 1<=qty<=s['guests']:raise ValueError('Invalid activity or quantity')
                    if iso(a['starts'])<datetime.now(timezone.utc):raise ValueError('Activity already started')
                    booked=c.execute('SELECT COALESCE(SUM(qty),0) FROM bookings WHERE activity_id=?',(activity,)).fetchone()[0]
                    if booked+qty>a['capacity']:raise ValueError('Not enough available spaces')
                    if c.execute('SELECT 1 FROM bookings WHERE stay_id=? AND activity_id=?',(s['id'],activity)).fetchone():raise ValueError('Already registered for this activity')
                    cur=c.execute('INSERT INTO bookings(stay_id,activity_id,qty,created) VALUES(?,?,?,?)',(s['id'],activity,qty,now_iso()))
                    if a['price']>0:c.execute('INSERT INTO folio_charges(stay_id,source,reference_id,description,amount_cents,created) VALUES(?,?,?,?,?,?)',(s['id'],'activity',cur.lastrowid,a['title']+' × '+str(qty),round(a['price']*qty*100),now_iso()))
                    self.respond({'id':cur.lastrowid},201);return
            if path=='/api/cancel-booking':
                with conn() as c:
                    c.execute('BEGIN IMMEDIATE');s=getstay(c,str(b.get('token','')))
                    bk=c.execute('SELECT id FROM bookings WHERE stay_id=? AND activity_id=?',(s['id'],int(b.get('activity_id',0)))).fetchone()
                    if bk:
                        charge=c.execute("SELECT status FROM folio_charges WHERE source='activity' AND reference_id=?",(bk['id'],)).fetchone()
                        if charge and charge['status']=='settled':raise ValueError('Cannot cancel a paid activity; contact reception')
                        c.execute("UPDATE folio_charges SET status='void' WHERE source='activity' AND reference_id=?",(bk['id'],))
                        c.execute('DELETE FROM bookings WHERE id=?',(bk['id'],))
                self.respond({'ok':True});return
            if path=='/api/chat/guest':
                body=str(b.get('message','')).strip()
                if not body or len(body)>1500:raise ValueError('Message must be between 1 and 1500 characters')
                with conn() as c:
                    stay=getstay(c,str(b.get('token','')))
                    cur=c.execute('INSERT INTO chat_messages(stay_id,sender,body,created) VALUES(?,?,?,?)',(stay['id'],'guest',body,now_iso()))
                self.respond({'id':cur.lastrowid,'ok':True},201);return
            if path=='/api/chat/admin':
                body=str(b.get('message','')).strip()
                if not body or len(body)>1500:raise ValueError('Message must be between 1 and 1500 characters')
                stay_id=int(b.get('stay_id',0))
                with conn() as c:
                    stay=c.execute('SELECT * FROM stays WHERE id=?',(stay_id,)).fetchone()
                    if not stay:raise ValueError('Room conversation not found')
                    if not stay_valid(stay):raise ValueError('Stay has expired or access is revoked')
                    cur=c.execute('INSERT INTO chat_messages(stay_id,sender,body,created) VALUES(?,?,?,?)',(stay_id,'reception',body,now_iso()))
                self.respond({'id':cur.lastrowid,'ok':True},201);return
            if path=='/api/status':
                status=str(b.get('status',''));id=int(b.get('id',0))
                if status not in ('new','progress','done'):raise ValueError('Invalid status')
                with conn() as c:c.execute('UPDATE requests SET status=? WHERE id=?',(status,id))
                self.respond({'ok':True});return
            if path=='/api/revoke':
                with conn() as c:c.execute('UPDATE stays SET revoked=1 WHERE id=?',(int(b.get('id',0)),))
                self.respond({'ok':True});return
            self.fail('Not found',404)
        except (ValueError,sqlite3.IntegrityError,TypeError) as e:self.fail(e)
        except Exception as e:self.fail(str(e),500)

if __name__=='__main__':
    initialize()
    print(f'STAYOS admin: http://localhost:{PORT}')
    print(f'Phone test URL: {guest_base_url() or "UNKNOWN - set STAYOS_GUEST_BASE_URL"}')
    print('WARNING: pilot demo only; review security before real hotel usage.')
    ThreadingHTTPServer(('0.0.0.0',PORT),Handler).serve_forever()
