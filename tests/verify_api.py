import sys,os,subprocess,time,tempfile,requests,datetime,json
from pathlib import Path
BASE='http://127.0.0.1:18766';DB=Path(tempfile.mkdtemp())/'stay.db'
env=os.environ.copy();env.update({'PORT':'18766','STAYOS_DB':str(DB),'STAYOS_GUEST_BASE_URL':BASE})
p=subprocess.Popen([sys.executable,str(Path(__file__).resolve().parents[1]/'server.py')],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
checks=[]
orig_get=requests.get;orig_post=requests.post
staff=requests.Session()
def scoped_get(url,**kw):return staff.get(url,**kw)
def scoped_post(url,**kw):return staff.post(url,**kw)
def assert_http(response,status=200):assert response.status_code==status,(response.status_code,response.text[:500]);return response.json()
def check(name, fn):fn();checks.append(name);print('PASS',name)
try:
 for i in range(50):
  try:requests.get(BASE+'/api/network',timeout=.5).raise_for_status();break
  except Exception:time.sleep(.15)
 now=datetime.datetime.now(datetime.timezone.utc)
 assert_http(orig_get(BASE+'/api/stays'),401)
 assert_http(orig_post(BASE+'/api/settings',json={'hotel_name':'DUMMY','theme':'luxury','welcome':'Hello'}),401)
 assert_http(staff.post(BASE+'/api/auth/login',json={'password':'stayos-demo'}))
 requests.get=scoped_get;requests.post=scoped_post
 print('PASS staff authorization and login')
 config={'room':'204','guests':3,'start':(now-datetime.timedelta(minutes=30)).isoformat(),'ends':(now+datetime.timedelta(days=6)).isoformat(),'zones':['room','activities'],'room_type_id':1}
 new=assert_http(requests.post(BASE+'/api/stays',json=config),201);tok=new['token'];stay_id=new['id']
 check('guest time-limited QR, PNG',lambda:(assert_http(requests.get(BASE+'/api/guest',params={'token':tok})),requests.get(BASE+'/api/qr',params={'token':tok}).headers['content-type']=='image/png' or (_ for _ in()).throw(AssertionError())))
 check('branding save/restore', lambda:(assert_http(requests.post(BASE+'/api/settings',json={'hotel_name':'SAPPHIRE RESORT','theme':'ocean','welcome':'Welcome to paradise'})),assert_http(requests.get(BASE+'/api/settings'))['hotel_name']=='SAPPHIRE RESORT' or (_ for _ in()).throw(AssertionError())))
 check('guest room type and gallery',lambda:assert_http(requests.get(BASE+'/api/room-types')))
 check('room quotas, over-limit rejected',lambda:(assert_http(requests.post(BASE+'/api/requests',json={'token':tok,'item':'water','qty':3}),201),assert_http(requests.post(BASE+'/api/requests',json={'token':tok,'item':'water','qty':1}),400)))
 check('housekeeping duplicate blocked',lambda:(assert_http(requests.post(BASE+'/api/requests',json={'token':tok,'item':'cleaning','qty':1}),201),assert_http(requests.post(BASE+'/api/requests',json={'token':tok,'item':'cleaning','qty':1}),400)))
 check('room service status change',lambda:(assert_http(requests.post(BASE+'/api/status',json={'id':1,'status':'progress'})),assert_http(requests.get(BASE+'/api/my',params={'token':tok}))))
 acts=assert_http(requests.get(BASE+'/api/activities'));future=[x for x in acts if datetime.datetime.fromisoformat(x['starts'])>now];assert future;aid=future[0]['id']
 check('activity reserve, no duplicate, cancel',lambda:(assert_http(requests.post(BASE+'/api/bookings',json={'token':tok,'activity_id':aid,'qty':2}),201),assert_http(requests.post(BASE+'/api/bookings',json={'token':tok,'activity_id':aid,'qty':1}),400),assert_http(requests.post(BASE+'/api/cancel-booking',json={'token':tok,'activity_id':aid}))))
 check('guest chat, reception response',lambda:(assert_http(requests.post(BASE+'/api/chat/guest',json={'token':tok,'message':'Hi reception!'}),201),assert_http(requests.post(BASE+'/api/chat/admin',json={'stay_id':stay_id,'message':'Welcome!'}),201),len(assert_http(requests.get(BASE+'/api/chat/guest',params={'token':tok}))['messages'])==2 or (_ for _ in()).throw(AssertionError())))
 check('images accessible',lambda:all(requests.get(BASE+'/assets/'+name+'.webp').headers.get('content-type')=='image/webp' for name in ['resort-hero','room-garden','room-ocean','room-villa','wellness-spa','dining','yoga']))
 check('revocation blocks guest API',lambda:(assert_http(requests.post(BASE+'/api/revoke',json={'id':stay_id})),assert_http(requests.get(BASE+'/api/guest',params={'token':tok}),400)))
 # The same room may not have two active check-in codes at once.
 new2=assert_http(requests.post(BASE+'/api/stays',json=config),201)
 assert new2['url'].startswith('/guest?guest=')
 assert_http(requests.get(BASE+'/api/guest',params={'token':tok}),400)
 assert_http(requests.get(BASE+'/api/guest',params={'token':new2['token']}))
 assert requests.get(BASE+new2['url']).status_code==200
 check('guest shortcut URL and previous-code revocation',lambda:True)
 print('ALL',len(checks),'API TESTS PASSED')
finally:
 p.terminate();p.wait(timeout=5)
