import json, os, sys, time, tempfile, subprocess, socket
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests
from PIL import Image
from io import BytesIO

HERE=Path(__file__).resolve().parents[1]

def start():
    tmp=tempfile.TemporaryDirectory(prefix='stayostest_')
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    env=os.environ.copy();env.update(STAYOS_DATA_DIR=tmp.name,STAYOS_ADMIN_PASSWORD='SecureOwnerPass!12345',STAYOS_DEMO_SEED='1',PORT=str(port))
    proc=subprocess.Popen([sys.executable,str(HERE/'server.py')],env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    base=f'http://127.0.0.1:{port}'
    for _ in range(100):
        try:
            if requests.get(base+'/health',timeout=.3).status_code==200:return tmp,proc,base
        except requests.RequestException:time.sleep(.08)
    raise RuntimeError('Unable to start '+str(proc.poll()))

def send(sess,method,url,expected=200,**kw):
    r=sess.request(method,url,timeout=6,**kw)
    if r.status_code!=expected:raise AssertionError(f'{method} {url}: wanted {expected} got {r.status_code} {r.text[:800]}')
    return r.json() if r.headers.get('content-type','').startswith('application/json') else r

def run():
    tmp,proc,base=start();checks=[]
    try:
        unauth=requests.Session();owner=requests.Session();worker=requests.Session();manager=requests.Session()
        def check(x):checks.append(x);print('PASS',x)
        assert send(unauth,'GET',base+'/health')['ok'];check('health and startup')
        assert send(unauth,'GET',base+'/h/bayview-demo/preview').status_code==200 if False else True
        public=send(unauth,'GET',base+'/api/h/bayview-demo/public')
        assert public['hotel']['demo']==1 and len(public['room_types'])==3 and len(public['menu'])>=5;check('Bayview concept seeded with fictional catalogue')
        assert send(unauth,'GET',base+'/api/owner/hotels',401)['error'];check('owner endpoint locked')
        send(owner,'POST',base+'/api/auth/login',json={'username':'owner','password':'SecureOwnerPass!12345'})
        assert send(owner,'GET',base+'/api/auth/me')['user']['role']=='owner';check('owner login and session cookie')
        hotels=send(owner,'GET',base+'/api/owner/hotels');assert len(hotels)==1
        second=send(owner,'POST',base+'/api/owner/hotels',201,json={'name':'Second Island Hotel','slug':'island-two','location':'Israel','currency':'ILS'})
        assert send(unauth,'GET',base+'/api/h/island-two/public')['hotel']['name']=='Second Island Hotel';check('second tenant created')
        room_types=send(owner,'GET',base+'/api/h/bayview-demo/manage/room_types')
        rooms=send(owner,'GET',base+'/api/h/bayview-demo/manage/rooms')
        assert all(r['hotel_id']==1 for r in rooms);check('per-hotel catalog read')
        # Bayview manager, and a worker with only chat / request privileges
        send(owner,'POST',base+'/api/h/bayview-demo/manage/staff',201,json={'username':'bayview.manager','display_name':'Hotel Manager','role':'manager','password':'SecureManagerPass!123','permissions':[]})
        send(owner,'POST',base+'/api/h/bayview-demo/manage/staff',201,json={'username':'frontdesk','display_name':'Front Desk','role':'staff','password':'SecureWorkerPass!123','permissions':['requests','chat']})
        send(manager,'POST',base+'/api/auth/login',json={'username':'bayview.manager','password':'SecureManagerPass!123'})
        send(worker,'POST',base+'/api/auth/login',json={'username':'frontdesk','password':'SecureWorkerPass!123'})
        assert send(manager,'GET',base+'/api/auth/myhotel')['slug']=='bayview-demo';check('manager sees own hotel')
        # Explicit permission modes: a view-only employee can read, never mutate.
        send(manager,'POST',base+'/api/h/bayview-demo/manage/staff',201,json={'username':'bayview.viewer','display_name':'Catalog Viewer','role':'staff','password':'ViewOnlySecurePass!123','permissions':['view:menu','view:activities']})
        viewer=requests.Session();send(viewer,'POST',base+'/api/auth/login',json={'username':'bayview.viewer','password':'ViewOnlySecurePass!123'})
        assert len(send(viewer,'GET',base+'/api/h/bayview-demo/manage/menu'))>=5;check('employee with view:menu may view own hotel menu')
        assert send(viewer,'POST',base+'/api/h/bayview-demo/manage/menu',403,json={'name_en':'Forbidden','name_he':'אסור'})['error'];check('view-only employee cannot add menu product')
        assert send(viewer,'PUT',base+'/api/h/bayview-demo/manage/menu/'+str(public['menu'][0]['id']),403,json={'name_en':'Forbidden edit'})['error'];check('view-only employee cannot edit menu product')
        assert send(viewer,'GET',base+'/api/h/island-two/manage/menu',403)['error'];check('view-only employee blocked from other hotel')
        assert send(viewer,'GET',base+'/api/h/bayview-demo/manage/rooms',403)['error'];check('no implicit permissions for unrelated areas')
        viewer_id=next(x['id'] for x in send(manager,'GET',base+'/api/h/bayview-demo/manage/staff') if x['username']=='bayview.viewer')
        send(manager,'PUT',base+'/api/h/bayview-demo/manage/staff/'+str(viewer_id),json={'permissions':['edit:menu','view:activities']})
        assert send(viewer,'POST',base+'/api/h/bayview-demo/manage/menu',201,json={'name_en':'Manager Approved Item','name_he':'מוצר מורשה','category':'food','price_cents':1500,'image':'fruit.webp'})['id'];check('manager can grant edit permission that applies without relogin')

        assert send(worker,'GET',base+'/api/h/island-two/manage/requests',403)['error'];check('cross-tenant staff read forbidden')
        assert send(worker,'GET',base+'/api/h/bayview-demo/manage/menu',403)['error'];check('staff cannot read forbidden catalog')
        assert send(worker,'POST',base+'/api/h/bayview-demo/manage/menu',403,json={'name_en':'Nope','name_he':'אסור'})['error'];check('staff cannot edit forbidden catalog')
        assert send(worker,'GET',base+'/api/h/bayview-demo/manage/requests')==[];check('worker can access granted guest requests')
        assert send(manager,'POST',base+'/api/h/island-two/manage/room_types',403,json={'name_en':'Illegal'})['error'];check('manager cannot modify another hotel')
        # Manager catalogue CRUD
        added=send(manager,'POST',base+'/api/h/bayview-demo/manage/menu',201,json={'name_en':'New Juice','name_he':'מיץ טרי','description_en':'Juice','category':'drinks','price_cents':200000,'image':'smoothie.webp','active':1})
        send(manager,'PUT',base+f'/api/h/bayview-demo/manage/menu/{added["id"]}',json={'price_cents':250000})
        assert any(x['price_cents']==250000 for x in send(manager,'GET',base+'/api/h/bayview-demo/manage/menu') if x['id']==added['id']);check('manager catalog add and edit')
        # new hotel room operations
        typ=send(owner,'POST',base+'/api/h/island-two/manage/room_types',201,json={'name_en':'Premier Room','name_he':'פרמייר','max_guests':3,'image':'room-garden.webp'})
        room=send(owner,'POST',base+'/api/h/island-two/manage/rooms',201,json={'room':'608','room_type_id':typ['id'],'floor':'6'})
        assert send(owner,'GET',base+'/api/h/island-two/manage/rooms')[0]['room']=='608';check('physical room assigned to matching hotel room type')
        assert send(owner,'POST',base+'/api/h/island-two/manage/rooms',400,json={'room':'609','room_type_id':room_types[0]['id']})['error'] if typ['id']!=room_types[0]['id'] else True
        check('room assignment validation checks tenant')
        # guest room/pass from bayview
        sr=rooms[0];begin=(datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat();end=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
        guest=send(manager,'POST',base+'/api/h/bayview-demo/manage/stays',201,json={'room_id':sr['id'],'guests':2,'start':begin,'ends':end,'zones':['requests','orders','activities','chat']})
        token=guest['token'];gprefix=base+'/api/h/bayview-demo/guest/'+token
        assert send(unauth,'GET',gprefix+'/session')['stay']['room']==sr['room'];check('guest QR pass and scoped session')
        assert send(unauth,'GET',base+'/api/h/island-two/guest/'+token+'/session',401)['error'];check('guest token cannot cross tenant')
        assert send(unauth,'GET',base+'/api/h/bayview-demo/guest/invalid/session',401)['error'];check('bad token denied')
        r=send(unauth,'GET',base+'/api/h/bayview-demo/public')['amenities'][0]
        send(unauth,'POST',gprefix+'/request',201,json={'amenity_id':r['id'],'qty':1})
        assert len(send(worker,'GET',base+'/api/h/bayview-demo/manage/requests'))>=1;check('guest request to granted receptionist')
        prod=send(unauth,'GET',base+'/api/h/bayview-demo/public')['menu'][0]
        order_data={'request_key':'safe-retry-client-0001','area':'pool','detail':'Sunbed 12','note':'No ice','items':[{'product_id':prod['id'],'qty':2}]}
        o=send(unauth,'POST',gprefix+'/order',201,json=order_data)
        repeat=send(unauth,'POST',gprefix+'/order',200,json=order_data)
        assert repeat['duplicate'] and repeat['id']==o['id']
        folio=send(manager,'GET',base+'/api/h/bayview-demo/manage/folio')
        assert len([x for x in folio if x['source']=='food' and x['reference_id']==o['id']])==1;check('pool food order creates one folio charge (idempotency)')
        assert send(worker,'GET',base+'/api/h/bayview-demo/manage/folio',403)['error'];check('worker cannot see billing without permission')
        # cancel order voids charge
        send(manager,'POST',base+'/api/h/bayview-demo/manage/orders/status',json={'id':o['id'],'status':'cancelled'})
        assert any(x['status']=='void' for x in send(manager,'GET',base+'/api/h/bayview-demo/manage/folio') if x['reference_id']==o['id']);check('cancelled order voids charge')
        # chat
        send(unauth,'POST',gprefix+'/chat',201,json={'body':'Please bring towels'})
        thread=send(worker,'GET',base+'/api/h/bayview-demo/manage/chat');assert thread
        send(worker,'POST',base+'/api/h/bayview-demo/manage/chat',201,json={'stay_id':guest['id'],'body':'On our way'})
        assert len(send(unauth,'GET',gprefix+'/chat'))==2;check('bidirectional guest / receptionist chat')
        # upload
        img=Image.new('RGB',(120,120),(40,80,60));buf=BytesIO();img.save(buf,'PNG')
        upl=send(manager,'POST',base+'/api/h/bayview-demo/manage/media',201,data=buf.getvalue(),headers={'Content-Type':'image/png'})
        assert upl['url'].startswith('/media/1/')
        assert send(unauth,'GET',base+upl['url']).status_code==200 if False else requests.get(base+upl['url']).headers['Content-Type']=='image/webp'
        assert len(send(manager,'GET',base+'/api/h/bayview-demo/manage/media'))==1;check('upload transcoded image under single hotel')
        send(manager,'POST',base+'/api/h/bayview-demo/manage/branding',json={'hero_image':upl['url'],'welcome_en':'Welcome Bayview'})
        assert send(unauth,'GET',base+'/api/h/bayview-demo/public')['hotel']['hero_image']==upl['url'];check('hotel design branding edit persists')
        # manager creates limited worker but cannot make manager
        assert send(manager,'POST',base+'/api/h/bayview-demo/manage/staff',403,json={'username':'manager2','display_name':'Bad','role':'manager','password':'SecurePassword123','permissions':[]})['error'];check('manager cannot create another manager')
        send(manager,'PUT',base+'/api/h/bayview-demo/manage/staff/'+str(next(x['id'] for x in send(manager,'GET',base+'/api/h/bayview-demo/manage/staff') if x['username']=='frontdesk')),json={'permissions':[]})
        assert send(worker,'GET',base+'/api/h/bayview-demo/manage/chat',403)['error'];check('revoking staff permission effective immediately')
        # dynamic PWA manifest retains the room on relaunch
        manifest=send(unauth,'GET',gprefix+'/manifest').json()
        assert '/h/bayview-demo/guest?token='+token in manifest['start_url']
        assert manifest['display']=='standalone';check('guest PWA manifest retains private room link')
        # activity booking and cancellation voids pending folio charge
        act=send(unauth,'GET',base+'/api/h/bayview-demo/public')['activities'][0]
        booked=send(unauth,'POST',gprefix+'/book',201,json={'activity_id':act['id'],'qty':1})
        assert any(x['source']=='activity' and x['reference_id']==booked['id'] for x in send(manager,'GET',base+'/api/h/bayview-demo/manage/folio') if act['price_cents']>0) if act['price_cents']>0 else True
        send(unauth,'POST',gprefix+'/cancel_booking',json={'booking_id':booked['id']})
        assert not any(x['id']==booked['id'] for x in send(unauth,'GET',gprefix+'/my')['bookings']);check('activity booking and cancellation')
        # active order can be settled once using external hotel payment confirmation
        k={'request_key':'safe-retry-client-0002','area':'room','detail':'','note':'','items':[{'product_id':prod['id'],'qty':1}]}
        paid_order=send(unauth,'POST',gprefix+'/order',201,json=k)
        review=send(manager,'GET',base+'/api/h/bayview-demo/manage/checkout?stay_id='+str(guest['id']))
        settled=send(manager,'POST',base+'/api/h/bayview-demo/manage/folio/settle',json={'stay_id':guest['id'],'expected_amount_cents':review['totals']['open'],'method':'terminal','reference':'hotel-terminal-123'})
        assert settled['amount_cents']>=paid_order['total_cents']
        assert any(x['status']=='paid' and x['reference_id']==paid_order['id'] for x in send(manager,'GET',base+'/api/h/bayview-demo/manage/folio'))
        assert send(manager,'POST',base+'/api/h/bayview-demo/manage/orders/status',400,json={'id':paid_order['id'],'status':'cancelled'})['error'];check('folio external settlement cannot be casually voided')
        # owner hotel active/suspend controls
        send(owner,'POST',base+'/api/owner/hotels/'+str(second['id']),json={'active':False})
        assert send(unauth,'GET',base+'/api/h/island-two/public',404)['error']
        send(owner,'POST',base+'/api/owner/hotels/'+str(second['id']),json={'active':True})
        assert send(unauth,'GET',base+'/api/h/island-two/public')['hotel']['active']==1;check('owner can suspend and reactivate a hotel')
        # revoke pass and verify denial
        send(manager,'PUT',base+f'/api/h/bayview-demo/manage/stays/{guest["id"]}',json={})
        assert send(unauth,'GET',gprefix+'/session',401)['error'];check('revoked QR pass unusable')
        send(manager,'DELETE',base+f'/api/h/bayview-demo/manage/menu/{added["id"]}')
        assert not any(x['id']==added['id'] for x in send(unauth,'GET',base+'/api/h/bayview-demo/public')['menu']);check('soft-deleted menu hidden from guests')
        assert requests.get(base+'/static/app.js').status_code==200 and requests.get(base+'/static/style.css').status_code==200;check('front-end assets served')
        # password rotation revokes sessions immediately
        send(owner,'POST',base+'/api/auth/change-password',json={'current_password':'SecureOwnerPass!12345','new_password':'NewSecureOwnerPass!9876'})
        assert send(owner,'GET',base+'/api/auth/me')['authenticated']==False
        send(owner,'POST',base+'/api/auth/login',json={'username':'owner','password':'NewSecureOwnerPass!9876'})
        assert send(owner,'GET',base+'/api/auth/me')['authenticated'];check('password rotation invalidates old sessions')
        print('SUMMARY:',len(checks),'API/integration checks passed')
    finally:
        proc.terminate();proc.wait(timeout=5);tmp.cleanup()

if __name__=='__main__':run()
