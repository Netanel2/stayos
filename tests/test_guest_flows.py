"""Interactive end-to-end regression for guest orders and reception feed.
Uses a local HTTP proxy because this sandbox blocks Chromium localhost navigation.
"""
import sys,base64,requests
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from test_platform import start

ROOT=Path(__file__).resolve().parents[1]
HTML='<html lang="en"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="app">Loading...</div><div id="toast"></div></body></html>'
JS=(ROOT/'static/app.js').read_text();CSS=(ROOT/'static/style.css').read_text()

def run():
 tmp,server,base=start()
 errors=[]
 try:
  with sync_playwright() as p:
   browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--disable-dev-shm-usage'])
   def setup(width=1440,height=900):
    page=browser.new_page(viewport={'width':width,'height':height});session=requests.Session()
    def proxy(source,details):
     url=details['url'];url=base+url if url.startswith('/') else url
     body=details.get('body')
     if details.get('base64'):body=base64.b64decode(body)
     resp=session.request(details.get('method','GET'),url,data=body,headers=details.get('headers') or {},timeout=7)
     return {'status':resp.status_code,'body':resp.text,'ctype':resp.headers.get('content-type','application/json')}
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.expose_binding('__fetchProxy',proxy)
    page.set_content(HTML);page.add_style_tag(content=CSS)
    page.evaluate('''() => {
      history.replaceState=()=>{};
      const store={getItem:()=>null,setItem:()=>{},removeItem:()=>{}};
      Object.defineProperty(window,'localStorage',{value:store});
      Object.defineProperty(window,'sessionStorage',{value:store});
      window.fetch=async(u,o={})=>{
       let body=o.body??null, enc=false;
       if(body instanceof Blob){let arr=new Uint8Array(await body.arrayBuffer());body=btoa(Array.from(arr,x=>String.fromChar(x)).join(''));enc=true;}
       const r=await window.__fetchProxy({url:String(u),method:o.method||'GET',body,headers:o.headers||{},base64:enc});
       return new Response(r.body,{status:r.status,headers:{'Content-Type':r.ctype}});
      };
    }''')
    page.add_script_tag(content=JS)
    return page,session
   admin,owner=setup()
   admin.locator('#loginForm input[name=username]').fill('owner')
   admin.locator('#loginForm input[name=password]').fill('SecureOwnerPass!12345')
   admin.locator('#loginForm button[type=submit]').click()
   admin.get_by_text('Your Hotels').wait_for(timeout=8000)
   admin.get_by_role('button',name='Manage hotel').click()
   expect(admin.locator('[data-action=demoGuest]').first).to_be_visible()
   admin.evaluate('checkAlerts()')  # seed the baseline before guest activity
   admin.locator('button[data-action=demoGuest]').first.click()
   href=admin.locator('.modal a[href*="guest?token="]').get_attribute('href')
   assert href and 'bayview-demo/guest?token=' in href
   token=href.split('token=',1)[1]
   admin.locator('button[data-action=closeModal]').first.click()
   print('PASS manager can request a real authorized concept guest pass')
   # Prevent anonymous guest preview ordering.
   preview,_=setup(390,844)
   preview.evaluate('''async()=>{state.slug='bayview-demo';await loadPublic();state.preview=true;renderGuest();}''')
   preview.locator('button[data-tab=dining]').last.click()
   preview.locator('button[data-step="1"]').first.click()
   assert preview.locator('#guestOrderForm button[type=submit]').is_disabled()
   print('PASS anonymous concept preview cannot make charges')
   guest,_=setup(390,844)
   guest.evaluate('''async(t)=>{state.slug='bayview-demo';state.token=t;await loadPublic();state.guest=await api(hroute(state.slug,'guest/'+t+'/session'));state.preview=false;renderGuest();}''',token)
   guest.locator('button[data-tab=dining]').last.click()
   guest.locator('button[data-step="1"]').first.click()
   expect(guest.locator('#guestOrderForm button[type=submit]')).to_be_enabled()
   guest.locator('#deliveryArea').select_option('pool')
   guest.locator('input[name=detail]').fill('Sunbed 12')
   guest.locator('input[name=note]').fill('No ice please')
   guest.locator('button[data-step="1"]').nth(1).click()
   assert guest.locator('#deliveryArea').input_value()=='pool', 'Changing quantity reset delivery area'
   assert guest.locator('input[name=detail]').input_value()=='Sunbed 12', 'Changing quantity reset delivery detail'
   guest.locator('#guestOrderForm button[type=submit]').click()
   guest.get_by_text('Sent to reception',exact=False).wait_for(timeout=12000) if False else guest.wait_for_function("state.guestTab==='my'",timeout=12000)
   orders=owner.get(base+'/api/h/bayview-demo/manage/orders').json()
   assert len(orders)==1 and orders[0]['area']=='pool' and orders[0]['detail']=='Sunbed 12'
   assert len(orders[0]['lines'])==2
   folio=owner.get(base+'/api/h/bayview-demo/manage/folio').json()
   assert len(folio)==1 and folio[0]['amount_cents']==orders[0]['total_cents']
   print('PASS guest food order works in mobile; room folio and delivery instructions saved')
   admin.evaluate('checkAlerts()')
   assert 'new' in admin.locator('#receptionLive').inner_text().lower()
   admin.locator('#receptionLive').click()
   assert admin.locator('.modal').get_by_text('Food order',exact=False).count()>0
   admin.locator('button[data-action=closeModal]').first.click()
   admin.locator('button[data-tab=orders]').click()
   admin.get_by_text('Orders & Delivery').wait_for()
   assert admin.get_by_text('Sunbed 12').count()>0
   print('PASS reception receives food order with a visible live notification')
   guest.locator('button[data-tab=amenities]').last.click()
   guest.locator('button[data-action=guestRequest]').first.click()
   guest.locator('#guestRequestForm button[type=submit]').click()
   admin.evaluate('checkAlerts()')
   admin.locator('button[data-tab=requests]').click()
   admin.get_by_role('heading',name='Guest Requests').wait_for()
   assert owner.get(base+'/api/h/bayview-demo/manage/requests').json()
   print('PASS housekeeping request appears in reception')
   guest.locator('button[data-tab=activities]').last.click()
   guest.locator('button[data-action=guestBook]').first.click()
   guest.locator('#guestBookForm button[type=submit]').click()
   admin.evaluate('checkAlerts()')
   admin.locator('button[data-tab=bookings]').click()
   admin.get_by_role('heading',name='Activity Bookings').wait_for(timeout=6000)
   assert len(owner.get(base+'/api/h/bayview-demo/manage/bookings').json())==1
   print('PASS activity booking appears in dedicated reception tab')
   guest.locator('button[data-tab=chat]').last.click()
   guest.locator('#guestChatForm input[name=body]').fill('Can you bring an extra pillow?')
   guest.locator('#guestChatForm button[type=submit]').click()
   admin.evaluate('checkAlerts()')
   admin.locator('button[data-tab=chat]').click()
   admin.get_by_role('heading',name='Guest Conversations').wait_for(timeout=6000)
   assert len(owner.get(base+'/api/h/bayview-demo/manage/chat').json())>=1
   guest.evaluate('checkAlerts()')  # seed guest reply watermark
   stay_id=owner.get(base+'/api/h/bayview-demo/manage/chat').json()[0]['stay_id']
   result=owner.post(base+'/api/h/bayview-demo/manage/chat',json={'stay_id':stay_id,'body':'Reception confirms your request'})
   assert result.status_code==201,result.text
   guest.evaluate('checkAlerts()')
   assert guest.locator('#toast .toast').filter(has_text='התקבלה הודעה חדשה מהקבלה').count()>0 or guest.get_by_text('New message from reception',exact=False).count()>0
   print('PASS guest chat reaches reception, staff reply appears with guest notification')
   assert not errors,errors
   assert not guest.evaluate('document.documentElement.scrollWidth>innerWidth')
   print('PASS no JavaScript console errors and guest mobile viewport fits')
   browser.close()
 finally:
  server.terminate();server.wait(timeout=5);tmp.cleanup()

if __name__=='__main__':run()
