import sys,os,subprocess,time,tempfile,requests,datetime,io
from pathlib import Path
from PIL import Image
BASE='http://127.0.0.1:18772';DB=Path(tempfile.mkdtemp())/'test.db'
env=os.environ.copy();env.update(PORT='18772',STAYOS_DB=str(DB),STAYOS_GUEST_BASE_URL=BASE)
p=subprocess.Popen([sys.executable,str(Path(__file__).resolve().parents[1]/'server.py')],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
def call(sess,method,path,status=200,**kw):
 r=getattr(sess,method)(BASE+path,timeout=10,**kw);assert r.status_code==status,(path,status,r.status_code,r.text[:400]);return r.json()
try:
 staff=requests.Session(); guest=requests.Session()
 for _ in range(70):
  try:guest.get(BASE+'/api/settings',timeout=.3).raise_for_status();break
  except:time.sleep(.1)
 else:raise AssertionError('Server did not start '+str(p.stderr.read()))
 call(guest,'get','/api/hotel-rooms',401)
 call(guest,'post','/api/catalog',401,json={'section':'hotel_rooms','record':{'room':'123'}})
 call(staff,'post','/api/auth/login',json={'password':'stayos-demo'})
 orig=call(staff,'get','/api/hotel-rooms');assert len(orig)>=4
 roomtype=call(staff,'post','/api/catalog',json={'section':'room_types','record':{'name':'Sunset Family Suite','description':'Oceanfront suite with balcony','size':'72m²','max_guests':5,'image':'room-ocean.webp'}})['id']
 physical=call(staff,'post','/api/catalog',json={'section':'hotel_rooms','record':{'room':'608','room_type_id':roomtype,'floor':'6','notes':'Corner suite'}})['id']
 assert any(x['room']=='608' for x in call(staff,'get','/api/hotel-rooms'))
 call(staff,'post','/api/catalog',json={'section':'hotel_rooms','record':{'id':physical,'room':'608','room_type_id':roomtype,'floor':'6','notes':'VIP'}})
 call(staff,'post','/api/catalog',400,json={'section':'hotel_rooms','record':{'room':'608','room_type_id':roomtype,'floor':'6'}})
 now=datetime.datetime.now(datetime.timezone.utc)
 x=call(staff,'post','/api/stays',201,json={'room':'608','guests':4,'start':(now-datetime.timedelta(minutes=2)).isoformat(),'ends':(now+datetime.timedelta(days=2)).isoformat(),'zones':['room','activities'],'room_type_id':roomtype})
 assert call(guest,'get','/api/guest?token='+x['token'])['room']=='608'
 call(staff,'post','/api/catalog',400,json={'section':'hotel_rooms','action':'delete','record':{'id':physical}})
 prod=call(staff,'post','/api/catalog',json={'section':'menu_products','record':{'name':'Sunset Lemonade','name_he':'לימונדה בשקיעה','description':'Fresh lemon juice','description_he':'לימונדה טבעית','category':'drinks','price_cents':1900,'image':'smoothie.webp','available':1}})['id']
 assert any(x['id']==prod for x in call(guest,'get','/api/menu'))
 call(staff,'post','/api/catalog',json={'section':'menu_products','record':{'id':prod,'name':'Sunset Lemonade','name_he':'לימונדה בשקיעה','description':'Fresh lemon juice','description_he':'לימונדה טבעית','category':'drinks','price_cents':2000,'image':'smoothie.webp','available':1}})
 order=call(guest,'post','/api/orders',201,json={'token':x['token'],'delivery_area':'pool','delivery_detail':'Sunbed 17','request_key':'unique-test-request-998','items':[{'product_id':prod,'qty':2}]});assert order['total_cents']==4000
 call(staff,'post','/api/catalog',json={'section':'menu_products','action':'delete','record':{'id':prod}})
 assert not any(z['id']==prod for z in call(guest,'get','/api/menu'))
 act=call(staff,'post','/api/catalog',json={'section':'activities','record':{'title':'Cocktail Workshop','description':'Learn tropical cocktails','starts':(now+datetime.timedelta(hours=4)).isoformat(),'ends':(now+datetime.timedelta(hours=6)).isoformat(),'location':'Pool Bar','capacity':15,'price':35,'image':'yoga.webp'}})['id']
 assert any(y['id']==act for y in call(guest,'get','/api/activities'))
 call(guest,'post','/api/bookings',201,json={'token':x['token'],'activity_id':act,'qty':2})
 call(staff,'post','/api/catalog',400,json={'section':'activities','action':'delete','record':{'id':act}})
 pic=Image.new('RGB',(120,80),(75,130,100));buf=io.BytesIO();pic.save(buf,'PNG')
 res=call(staff,'post','/api/upload-asset',json=None,data=buf.getvalue(),headers={'Content-Type':'image/png'})
 assert res['image'].endswith('.webp') and guest.get(BASE+'/assets/'+res['image']).status_code==200
 call(guest,'post','/api/upload-asset',401,data=buf.getvalue(),headers={'Content-Type':'image/png'})
 amenity=call(staff,'post','/api/catalog',json={'section':'amenities','record':{'slug':'baby_kit','name':'Baby Kit','name_he':'ערכת תינוק','basis':'guest','period':'stay','image':'towels.svg','enabled':1}})
 assert amenity['id']=='baby_kit'
 assert any(z['id']=='baby_kit' for z in call(guest,'get','/api/amenities'))
 call(guest,'post','/api/requests',201,json={'token':x['token'],'item':'baby_kit','qty':4})
 call(guest,'post','/api/requests',400,json={'token':x['token'],'item':'baby_kit','qty':1})
 call(staff,'post','/api/catalog',json={'section':'amenities','action':'delete','record':{'id':'baby_kit'}})
 assert not any(z['id']=='baby_kit' for z in call(guest,'get','/api/amenities'))
 print('PASS v2.2: staff-only CRUD, room inventory, catalog price, room folio, activity bookings, historical integrity, photo upload')
finally:
 p.terminate();p.wait(timeout=5)
