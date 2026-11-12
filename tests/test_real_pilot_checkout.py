"""End-to-end real-hotel onboarding, room folio, free activities, and external collection.

Test data is ephemeral. No real hotel is contacted and no payment is processed.
"""
import os,sys,tempfile,subprocess,socket,time,secrets,requests
from pathlib import Path
from datetime import datetime,timezone,timedelta
from test_platform import send
ROOT=Path(__file__).resolve().parents[1]

def start_clean(data_dir,port=None,seed="0"):
 s=socket.socket();s.bind(('127.0.0.1',0));port=s.getsockname()[1];s.close()
 env=os.environ.copy();env.update(STAYOS_DATA_DIR=data_dir,STAYOS_ADMIN_PASSWORD='SecureOwnerPass!12345',STAYOS_DEMO_SEED=seed,PORT=str(port))
 p=subprocess.Popen([sys.executable,str(ROOT/'server.py')],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
 base='http://127.0.0.1:'+str(port)
 for _ in range(130):
  try:
   if requests.get(base+'/health',timeout=.3).ok:return p,base
  except requests.RequestException:time.sleep(.07)
 raise RuntimeError('Service failed on fresh DB: '+str(p.poll()))

def run():
 with tempfile.TemporaryDirectory(prefix='stay_real_') as folder:
  process,base=start_clean(folder)
  try:
   owner=requests.Session();guest=requests.Session();worker=requests.Session();other=requests.Session()
   assert send(guest,'GET',base+'/api/h/bayview-demo/public',404)['error']
   send(owner,'POST',base+'/api/auth/login',json={'username':'owner','password':'SecureOwnerPass!12345'})
   assert send(owner,'GET',base+'/api/owner/hotels')==[]
   print('PASS no demo, hotel inventory, or artificial orders in new installation')
   a=send(owner,'POST',base+'/api/owner/hotels',201,json={'name':'Pilot Property','slug':'pilot-property','location':'Israel','currency':'ILS'})
   h=send(guest,'GET',base+'/api/h/pilot-property/public')['hotel']
   assert h['demo']==0 and h['hero_image']==''
   print('PASS real property can be created without fictional photos or prices')
   ap=base+'/api/h/pilot-property/'
   t=send(owner,'POST',ap+'manage/room_types',201,json={'name_en':'Garden Room','max_guests':3})
   room=send(owner,'POST',ap+'manage/rooms',201,json={'room':'306','room_type_id':t['id']})
   meal=send(owner,'POST',ap+'manage/menu',201,json={'name_en':'House Salad','price_cents':5900,'category':'food'})
   req=send(owner,'POST',ap+'manage/amenities',201,json={'name_en':'Extra Towels','basis':'room','quota':2,'period':'stay'})
   now=datetime.now(timezone.utc)
   free=send(owner,'POST',ap+'manage/activities',201,json={'name_en':'Morning Walk','price_cents':0,'capacity':10,'starts':(now+timedelta(hours=2)).isoformat(),'ends':(now+timedelta(hours=3)).isoformat()})
   paid=send(owner,'POST',ap+'manage/activities',201,json={'name_en':'Yoga Class','price_cents':3500,'capacity':10,'starts':(now+timedelta(hours=2)).isoformat(),'ends':(now+timedelta(hours=3)).isoformat()})
   stay=send(owner,'POST',ap+'manage/stays',201,json={'room_id':room['id'],'guests':2,'start':(now-timedelta(minutes=5)).isoformat(),'ends':(now+timedelta(days=1)).isoformat(),'zones':['requests','orders','activities','chat']})
   sid=stay['id']; gp=ap+'guest/'+stay['token']+'/'
   send(guest,'POST',gp+'request',201,json={'amenity_id':req['id'],'qty':1})
   send(guest,'POST',gp+'book',201,json={'activity_id':free['id'],'qty':2})
   send(guest,'POST',gp+'book',201,json={'activity_id':paid['id'],'qty':1})
   order=send(guest,'POST',gp+'order',201,json={'request_key':secrets.token_hex(20),'items':[{'product_id':meal['id'],'qty':2}],'area':'pool','detail':'Sunbed 19','note':'No onions'})
   assert order['total_cents']==11800
   stmt=send(owner,'GET',ap+'manage/checkout?stay_id='+str(sid))
   assert stmt['stay']['room']=='306'
   assert stmt['totals']['open']==15300 and stmt['totals']['paid']==0
   assert len(stmt['charges'])==2 and len(stmt['free_bookings'])==1 and len(stmt['requests'])==1
   assert any(x['delivery_area']=='pool' and x['delivery_detail']=='Sunbed 19' for x in stmt['charges'])
   assert stmt['payment_processing'] is False
   print('PASS delivery location, free activities, towels and two paid line items are in checkout statement')
   # Staff without folio access cannot access guest billing.
   send(owner,'POST',ap+'manage/staff',201,json={'username':'requestsonly','display_name':'Housekeeper','role':'staff','password':'SecureWorkerPass!123','permissions':['view:requests']})
   send(worker,'POST',base+'/api/auth/login',json={'username':'requestsonly','password':'SecureWorkerPass!123'})
   assert send(worker,'GET',ap+'manage/checkout?stay_id='+str(sid),403)['error']
   print('PASS room totals restricted by staff permission')
   # Incorrect expected amount should never mark money as collected.
   wrong=send(owner,'POST',ap+'manage/folio/settle',409,json={'stay_id':sid,'method':'cash','expected_amount_cents':15000})
   assert 'changed' in wrong['error'].lower()
   assert send(owner,'GET',ap+'manage/checkout?stay_id='+str(sid))['totals']['open']==15300
   print('PASS changed checkout total rejected without changing any charges')
   closed=send(owner,'POST',ap+'manage/folio/settle',json={'stay_id':sid,'method':'terminal','reference':'HOTEL-RECEIPT-1','expected_amount_cents':15300})
   assert closed['stay_closed'] and closed['amount_cents']==15300
   after=send(owner,'GET',ap+'manage/checkout?stay_id='+str(sid))
   assert after['totals']['open']==0 and after['totals']['paid']==15300
   assert len(after['settlements'])==1 and after['settlements'][0]['reference']=='HOTEL-RECEIPT-1'
   assert after['stay']['revoked']==1
   assert send(guest,'GET',gp+'session',401)['error']
   assert send(owner,'POST',ap+'manage/folio/settle',409,json={'stay_id':sid,'method':'cash','expected_amount_cents':0})['error']
   print('PASS external cash/terminal collection recorded once, QR access closed, repeat refused')
   other_hotel=send(owner,'POST',base+'/api/owner/hotels',201,json={'name':'Another Hotel','slug':'another-hotel','currency':'USD'})
   assert send(owner,'GET',base+'/api/h/another-hotel/manage/checkout?stay_id='+str(sid),404)['error']
   print('PASS checkout data is scoped to the correct hotel')
   # A guest who only requested free amenities can still be checked out safely.
   no_charges=send(owner,'POST',ap+'manage/stays',201,json={'room_id':room['id'],'guests':2,'start':(now-timedelta(minutes=2)).isoformat(),'ends':(now+timedelta(days=2)).isoformat(),'zones':['requests','orders','activities','chat']})
   empty_id=no_charges['id'];send(guest,'POST',ap+'guest/'+no_charges['token']+'/request',201,json={'amenity_id':req['id'],'qty':1})
   assert send(owner,'GET',ap+'manage/checkout?stay_id='+str(empty_id))['totals']['open']==0
   done=send(owner,'POST',ap+'manage/checkout/close',json={'stay_id':empty_id})
   assert done['stay_closed'] and done['amount_cents']==0
   assert not send(owner,'GET',ap+'manage/checkout?stay_id='+str(empty_id))['settlements']
   assert send(guest,'GET',ap+'guest/'+no_charges['token']+'/session',401)['error']
   assert send(owner,'POST',ap+'manage/checkout/close',409,json={'stay_id':empty_id})['error']
   print('PASS zero-balance checkout closes pass without recording any payment')
  finally:
   process.terminate();process.wait(timeout=7)
  # Verify collected records survive restart on same SQLite volume.
  process,base=start_clean(folder)
  try:
   owner=requests.Session();send(owner,'POST',base+'/api/auth/login',json={'username':'owner','password':'SecureOwnerPass!12345'})
   after=send(owner,'GET',base+'/api/h/pilot-property/manage/checkout?stay_id='+str(sid))
   assert after['totals']['paid']==15300 and len(after['settlements'])==1
   print('PASS external collection and room ledger survive server restart')
  finally:
   process.terminate();process.wait(timeout=7)
 # Existing sample hotels are archived and hidden after upgrade without data loss.
 with tempfile.TemporaryDirectory(prefix='stay_legacy_demo_') as folder:
  p,local=start_clean(folder,seed='1')
  try:
   assert send(requests.Session(),'GET',local+'/api/h/bayview-demo/public')['hotel']['demo']==1
  finally:p.terminate();p.wait(timeout=7)
  p,local=start_clean(folder,seed='0')
  try:
   owner=requests.Session();send(owner,'POST',local+'/api/auth/login',json={'username':'owner','password':'SecureOwnerPass!12345'})
   assert send(owner,'GET',local+'/api/owner/hotels')==[]
   assert send(requests.Session(),'GET',local+'/api/h/bayview-demo/public',404)['error']
   import sqlite3
   row=sqlite3.connect(Path(folder)/'platform.sqlite').execute('SELECT demo,active FROM hotels WHERE slug=?',('bayview-demo',)).fetchone()
   assert row==(1,0)
   print('PASS legacy concept hotel hidden/disabled without deleting old records')
  finally:p.terminate();p.wait(timeout=7)

if __name__=='__main__':run()
