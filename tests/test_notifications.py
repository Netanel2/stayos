"""Security and complete guest-to-reception notification regression."""
import sys,requests,secrets
from pathlib import Path
from test_platform import start,send

def run():
 tmp,server,base=start()
 try:
  staff=requests.Session();guest=requests.Session()
  url=base+'/api/h/bayview-demo/'
  send(staff,'POST',base+'/api/auth/login',json={'username':'owner','password':'SecureOwnerPass!12345'})
  assert send(guest,'POST',url+'manage/demo-pass',401,json={})['error']
  d=send(staff,'POST',url+'manage/demo-pass',200,json={})
  token=d['url'].split('token=')[-1];gu=url+'guest/'+token+'/'
  assert send(guest,'GET',gu+'session')['stay']['room']=='101'
  assert send(staff,'GET',url+'manage/events')==[]
  products=send(guest,'GET',url+'public')['menu']
  prod=products[0]
  for area in ['room','pool','lobby','beach','restaurant','garden']:
   info={'items':[{'product_id':prod['id'],'qty':1}], 'area':area,'detail':'' if area=='room' else 'Table 12','note':'No ice','request_key':secrets.token_hex(16)}
   saved=send(guest,'POST',gu+'order',201,json=info)
   assert saved['id']
  orders=send(staff,'GET',url+'manage/orders')
  assert len(orders)==6 and {o['area'] for o in orders}=={'room','pool','lobby','beach','restaurant','garden'}
  assert all(o['detail']=='Table 12' for o in orders if o['area']!='room')
  folio=send(staff,'GET',url+'manage/folio')
  assert len(folio)==6 and sum(x['amount_cents'] for x in folio)==sum(o['total_cents'] for o in orders)
  print('PASS six delivery locations, folio charge and correct details')
  feed=send(staff,'GET',url+'manage/events')
  assert len([r for r in feed if r['type']=='order'])==6
  print('PASS every order appears in permission-scoped notification feed')
  # Do not allow incomplete delivery instructions outside the room.
  invalid={'items':[{'product_id':prod['id'],'qty':1}], 'area':'pool','detail':'','note':'','request_key':secrets.token_hex(16)}
  assert 'location' in send(guest,'POST',gu+'order',400,json=invalid)['error'].lower()
  amenities=send(guest,'GET',url+'public')['amenities']
  send(guest,'POST',gu+'request',201,json={'amenity_id':amenities[0]['id'],'qty':1})
  assert len(send(staff,'GET',url+'manage/requests'))==1
  assert len([r for r in send(staff,'GET',url+'manage/events') if r['type']=='request'])==1
  print('PASS amenities request is visible in reception feed')
  activities=send(guest,'GET',url+'public')['activities']
  send(guest,'POST',gu+'book',201,json={'activity_id':activities[0]['id'],'qty':1})
  assert len(send(staff,'GET',url+'manage/bookings'))==1
  assert len([r for r in send(staff,'GET',url+'manage/events') if r['type']=='booking'])==1
  print('PASS activity booking visible in Activity Bookings and alerts')
  send(guest,'POST',gu+'chat',201,json={'body':'Please bring towels'})
  assert len([r for r in send(staff,'GET',url+'manage/events') if r['type']=='message'])==1
  sid=send(staff,'GET',url+'manage/chat')[0]['stay_id']
  send(staff,'POST',url+'manage/chat',201,json={'stay_id':sid,'body':'On our way'})
  assert send(guest,'GET',gu+'chat')[-1]['sender']=='reception'
  print('PASS chat reaches reception and reply reaches guest')
  send(staff,'POST',url+'manage/orders/status',json={'id':orders[0]['id'],'status':'preparing'})
  assert any(x['status']=='preparing' for x in send(staff,'GET',url+'manage/orders'))
  send(staff,'POST',url+'manage/orders/status',json={'id':orders[-1]['id'],'status':'cancelled'})
  assert any(x['status']=='void' for x in send(staff,'GET',url+'manage/folio'))
  print('PASS status updates and cancellation/folio void')
  # A staff member without access to orders must not receive order events.
  send(staff,'POST',url+'manage/staff',201,json={'username':'only.requests','display_name':'Limited worker','role':'staff','password':'LongPassword!123','permissions':['view:requests']})
  worker=requests.Session();send(worker,'POST',base+'/api/auth/login',json={'username':'only.requests','password':'LongPassword!123'})
  limited=send(worker,'GET',url+'manage/events')
  assert all(x['type']=='request' for x in limited),limited
  assert send(worker,'GET',url+'manage/bookings',403)['error']
  print('PASS notification feed obeys staff permissions')
  # Demo pass should never be available for ordinary properties.
  send(staff,'POST',base+'/api/owner/hotels',201,json={'slug':'ordinary-hotel','name':'Ordinary Hotel'})
  assert send(staff,'POST',base+'/api/h/ordinary-hotel/manage/demo-pass',403,json={})['error']
  print('PASS public/real hotel cannot create concept demo pass')
 finally:
  server.terminate();server.wait(timeout=5);tmp.cleanup()
if __name__=='__main__':run()
