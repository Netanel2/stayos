import sys,os,subprocess,time,tempfile,requests,datetime
from pathlib import Path
BASE='http://127.0.0.1:18769'; DB=Path(tempfile.mkdtemp())/'test.db'
env=os.environ.copy();env.update(PORT='18769',STAYOS_DB=str(DB),STAYOS_GUEST_BASE_URL=BASE)
p=subprocess.Popen([sys.executable,str(Path(__file__).resolve().parents[1]/'server.py')],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
def req(sess,method,path,expected=200,**kw):
 r=getattr(sess,method)(BASE+path,timeout=8,**kw);assert r.status_code==expected,(path,expected,r.status_code,r.text[:500]);return r.json()
try:
 staff=requests.Session();guest=requests.Session()
 for i in range(60):
  try: guest.get(BASE+'/api/menu',timeout=.2).raise_for_status();break
  except Exception:time.sleep(.1)
 req(guest,'get','/api/orders/admin',401)
 req(staff,'post','/api/auth/login',json={'password':'stayos-demo'})
 now=datetime.datetime.now(datetime.timezone.utc)
 s=req(staff,'post','/api/stays',201,json={'room':'204','guests':3,'start':(now-datetime.timedelta(hours=1)).isoformat(),'ends':(now+datetime.timedelta(days=4)).isoformat(),'zones':['room','activities'],'room_type_id':1})
 tok=s['token']; sid=s['id']
 menu=req(guest,'get','/api/menu');assert len(menu)>=8
 req(guest,'post','/api/orders',400,json={'token':tok,'request_key':'bad-location-001','delivery_area':'pool','delivery_detail':'','items':[{'product_id':1,'qty':2}]})
 req(guest,'post','/api/orders',400,json={'token':tok,'request_key':'bad-quantity-001','delivery_area':'room','items':[{'product_id':1,'qty':13}]})
 req(guest,'post','/api/orders',400,json={'token':tok,'request_key':'bad-duplicate-001','delivery_area':'room','items':[{'product_id':1,'qty':1},{'product_id':1,'qty':2}]})
 order=req(guest,'post','/api/orders',201,json={'token':tok,'request_key':'order-pool-001','delivery_area':'pool','delivery_detail':'Sunbed 12','note':'No ice','items':[{'product_id':1,'qty':2},{'product_id':4,'qty':1}]})
 assert order['total_cents']==2650,order
 assert len(req(staff,'get','/api/orders/admin'))==1
 duplicate=req(guest,'post','/api/orders',200,json={'token':tok,'request_key':'order-pool-001','delivery_area':'pool','delivery_detail':'Sunbed 12','items':[{'product_id':1,'qty':2},{'product_id':4,'qty':1}]})
 assert duplicate['duplicate'] and duplicate['id']==order['id']
 assert len(req(staff,'get','/api/orders/admin'))==1
 assert req(guest,'get','/api/orders/guest?token='+tok)[0]['delivery_detail']=='Sunbed 12'
 folio=req(staff,'get','/api/folio/admin');assert len(folio)==1 and folio[0]['amount_cents']==2650 and folio[0]['status']=='open'
 req(staff,'post','/api/orders/status',json={'id':order['id'],'status':'preparing'})
 req(staff,'post','/api/orders/status',json={'id':order['id'],'status':'cancelled'})
 assert req(staff,'get','/api/folio/admin')[0]['status']=='void'
 req(staff,'post','/api/orders/status',400,json={'id':order['id'],'status':'delivered'})
 new=req(guest,'post','/api/orders',201,json={'token':tok,'request_key':'order-lobby-001','delivery_area':'lobby','delivery_detail':'Table 4','items':[{'product_id':3,'qty':2}]})
 assert new['total_cents']==900
 acts=req(guest,'get','/api/activities');future=next((a for a in acts if datetime.datetime.fromisoformat(a['starts'])>now and a['price']>0),None)
 assert future,'Demo activity paid entry missing'
 reg=req(guest,'post','/api/bookings',201,json={'token':tok,'activity_id':future['id'],'qty':2})
 assert len([f for f in req(staff,'get','/api/folio/admin') if f['status']=='open'])==2
 req(guest,'post','/api/cancel-booking',json={'token':tok,'activity_id':future['id']})
 assert len([f for f in req(staff,'get','/api/folio/admin') if f['status']=='open'])==1
 my=req(guest,'get','/api/my?token='+tok)
 assert len(my['folio'])==3
 req(staff,'post','/api/folio/settle',400,json={'stay_id':sid,'method':'bogus'})
 settled=req(staff,'post','/api/folio/settle',json={'stay_id':sid,'method':'card_terminal'})
 assert settled['settled_cents']==900
 assert req(staff,'post','/api/folio/settle',json={'stay_id':sid,'method':'cash'})['settled_cents']==0
 req(staff,'post','/api/orders/status',400,json={'id':new['id'],'status':'cancelled'})
 req(guest,'get','/api/folio/admin',401)
 print('PASS 20+ assertions: guest ordering, destination, validation, staff delivery, cancellation/void, activity folio, checkout settlement, auth')
finally:
 p.terminate();p.wait(timeout=5)
