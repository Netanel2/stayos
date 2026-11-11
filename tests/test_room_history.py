"""Regression tests: per-room audit, delivery details, free activities, image replacement, tenant/permission scoping."""
import sys,requests,secrets
from io import BytesIO
from PIL import Image
from test_platform import start,send

def run():
 tmp,proc,base=start()
 try:
  owner=requests.Session();guest=requests.Session()
  send(owner,'POST',base+'/api/auth/login',json={'username':'owner','password':'SecureOwnerPass!12345'})
  prefix=base+'/api/h/bayview-demo/'
  passinfo=send(owner,'POST',prefix+'manage/demo-pass',200,json={})
  token=passinfo['url'].split('token=')[-1]
  gu=prefix+'guest/'+token+'/'
  prod=send(guest,'GET',prefix+'public')['menu'][0]
  ord=send(guest,'POST',gu+'order',201,json={'items':[{'product_id':prod['id'],'qty':2}], 'area':'pool','detail':'Sunbed 12','note':'Bring with ice','request_key':secrets.token_hex(18)})
  req=send(guest,'GET',prefix+'public')['amenities'][0]
  send(guest,'POST',gu+'request',201,json={'amenity_id':req['id'],'qty':1})
  acts=send(guest,'GET',prefix+'public')['activities']
  free=next(x for x in acts if x['price_cents']==0)
  booked=send(guest,'POST',gu+'book',201,json={'activity_id':free['id'],'qty':1})
  send(guest,'POST',gu+'chat',201,json={'body':'Can you bring more water?'})
  folio=send(owner,'GET',prefix+'manage/folio')
  assert folio[0]['delivery_area']=='pool' and folio[0]['delivery_detail']=='Sunbed 12'
  history=send(owner,'GET',prefix+'manage/room-history?room=101')
  events=history['entries'];kinds={e['type'] for e in events}
  assert {'order','request','booking','message','charge'}<=kinds,kinds
  order=next(e for e in events if e['type']=='order');assert order['id']==ord['id'] and order['area']=='pool' and order['detail']=='Sunbed 12' and order['note']=='Bring with ice' and len(order['items'])==1
  activity=next(e for e in events if e['type']=='booking');assert activity['price_cents']==0 and activity['status']=='booked'
  print('PASS complete room 101 records, delivery destination, guest message and free activity')
  # cancellation must keep the record and release capacity
  send(guest,'POST',gu+'cancel_booking',200,json={'booking_id':booked['id']})
  newhist=send(owner,'GET',prefix+'manage/room-history?room=101')['entries']
  assert next(e for e in newhist if e['type']=='booking')['status']=='cancelled'
  assert not send(guest,'GET',gu+'my')['bookings']
  send(guest,'POST',gu+'book',201,json={'activity_id':free['id'],'qty':1})
  assert next(e for e in send(owner,'GET',prefix+'manage/room-history?room=101')['entries'] if e['type']=='booking')['status']=='booked'
  print('PASS canceled free activity retained in record and can be rebooked')
  # new physical room doesn't leak another room's records
  second=send(owner,'GET',prefix+'manage/room-history?room=204')
  assert second['room']=='204' and not second['entries']
  rooms=send(owner,'GET',prefix+'manage/room-history')['rooms']
  assert {'101','204'}<=set(rooms)
  print('PASS room number search returns only exact room activity')
  # Staff with only room amenities view must not read orders/chat/activities/charges
  send(owner,'POST',prefix+'manage/staff',201,json={'username':'requests.history','display_name':'Housekeeping','role':'staff','password':'LongPassword!123','permissions':['view:requests']})
  limited=requests.Session();send(limited,'POST',base+'/api/auth/login',json={'username':'requests.history','password':'LongPassword!123'})
  resp=send(limited,'GET',prefix+'manage/room-history?room=101')
  assert {x['type'] for x in resp['entries']}=={'request'},resp
  assert resp['stays']==[]
  other=send(owner,'POST',base+'/api/owner/hotels',201,json={'slug':'test-hotel','name':'Other Hotel'})
  assert send(limited,'GET',base+'/api/h/test-hotel/manage/room-history?room=101',403)['error']
  print('PASS employee view-only permissions and cross-hotel security')
  # Photo editor API: replacing menu, activity, room type and branding images from hotel's own uploaded media
  photo=BytesIO();Image.new('RGB',(180,100),(31,119,73)).save(photo,format='PNG')
  upload=send(owner,'POST',prefix+'manage/media',201,data=photo.getvalue(),headers={'Content-Type':'image/png'})['url']
  assert upload.startswith('/media/')
  send(owner,'PUT',prefix+'manage/menu/'+str(prod['id']),json={'image':upload})
  typ=send(owner,'GET',prefix+'manage/room_types')[0]
  send(owner,'PUT',prefix+'manage/room_types/'+str(typ['id']),json={'image':upload})
  send(owner,'POST',prefix+'manage/branding',json={'hero_image':upload})
  phys=next(x for x in send(owner,'GET',prefix+'manage/rooms') if x['room']=='101')
  send(owner,'PUT',prefix+'manage/rooms/'+str(phys['id']),json={'image':upload})
  assert send(guest,'GET',gu+'session')['room_type']['image']==upload
  updated=send(guest,'GET',prefix+'public')
  assert next(x for x in updated['menu'] if x['id']==prod['id'])['image']==upload
  assert next(x for x in updated['room_types'] if x['id']==typ['id'])['image']==upload
  assert updated['hotel']['hero_image']==upload
  print('PASS photo upload and replacement for product, room type, physical room, and hotel hero')
  assert send(owner,'GET',prefix+'manage/room-history?room=101')['entries']
 finally:
  proc.terminate();proc.wait(timeout=5);tmp.cleanup()
if __name__=='__main__':run()
