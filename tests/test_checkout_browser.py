"""Chromium smoke test for the actual room checkout UI (no online payment)."""
import base64,secrets,requests
from playwright.sync_api import sync_playwright, expect
from test_platform import start
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
HTML='<html lang="en"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="app">Loading...</div><div id="toast"></div></body></html>'

def run():
 tmp,proc,base=start()
 try:
  with sync_playwright() as p:
   browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--disable-dev-shm-usage'])
   page=browser.new_page(viewport={'width':1200,'height':850});page.set_default_timeout(8000);http=requests.Session();errors=[]
   def proxy(source,detail):
    u=detail['url'];u=base+u if u.startswith('/') else u
    body=detail.get('body');headers=detail.get('headers') or {}
    if detail.get('base64'):body=base64.b64decode(body)
    r=http.request(detail.get('method','GET'),u,data=body,headers=headers,timeout=8)
    return {'status':r.status_code,'body':r.text,'ctype':r.headers.get('Content-Type','application/json')}
   page.on('pageerror',lambda e:errors.append(str(e)));page.expose_binding('__fetchProxy',proxy)
   page.set_content(HTML);page.add_style_tag(content=(ROOT/'static/style.css').read_text())
   page.evaluate('''()=>{history.replaceState=()=>{};const mem={};const store={getItem:k=>mem[k]||null,setItem:(k,v)=>mem[k]=v,removeItem:k=>delete mem[k]};Object.defineProperty(window,'localStorage',{value:store});Object.defineProperty(window,'sessionStorage',{value:store});window.fetch=async(u,o={})=>{let body=o.body??null;const r=await window.__fetchProxy({url:String(u),method:o.method||'GET',body,headers:o.headers||{}});return new Response(r.body,{status:r.status,headers:{'Content-Type':r.ctype}})}}''')
   page.add_script_tag(content=(ROOT/'static/app.js').read_text())
   page.locator('input[name="username"]').fill('owner');page.locator('input[name="password"]').fill('SecureOwnerPass!12345')
   page.locator('#loginForm button[type="submit"]').click();page.get_by_text('Your Hotels').wait_for()
   page.get_by_role('button',name='Manage hotel').first.click();page.get_by_text('Front desk command center').wait_for()
   api=base+'/api/h/bayview-demo/'
   d=http.post(api+'manage/demo-pass',json={}).json();token=d['url'].split('token=')[-1]
   prod=http.get(api+'public').json()['menu'][0]
   saved=http.post(api+'guest/'+token+'/order',json={'items':[{'product_id':prod['id'],'qty':1}],'area':'pool','detail':'Sunbed 27','note':'No ice','request_key':secrets.token_hex(16)}).json()
   assert saved['id']
   page.locator('button[data-tab="folio"]').click();page.get_by_text('Room Folio').wait_for()
   page.get_by_role('button',name='Review checkout').first.click();page.get_by_text('CHECKOUT STATEMENT').wait_for()
   expect(page.locator('.modal')).to_contain_text('Sunbed 27')
   expect(page.locator('.modal')).to_contain_text('Complimentary services & activities')
   expect(page.locator('.modal')).to_contain_text('To collect')
   expect(page.locator('#checkoutConfirmForm')).to_have_count(1)
   page.locator('input[name="reference"]').fill('HOTEL-OPS-27')
   page.locator('input[name="collected"]').check()
   page.once('dialog',lambda dialog:dialog.accept())
   page.locator('#checkoutConfirmForm button[type="submit"]').click()
   expect(page.locator('#modalroot .modal')).to_have_count(0)
   page.get_by_text('Review checkout').count()
   stmt=http.get(api+'manage/checkout?stay_id='+str(d['stay_id'] if 'stay_id' in d else next(x['id'] for x in http.get(api+'manage/stays').json() if x['room']=='101'))).json()
   assert any(x['reference']=='HOTEL-OPS-27' for x in stmt['settlements'])
   assert stmt['totals']['open']==0 and stmt['totals']['paid']>0
   assert not errors,errors
   page.set_viewport_size({'width':390,'height':844})
   page.locator('button[data-tab="folio"]').click();page.get_by_text('Room Folio').wait_for()
   assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
   print('PASS checkout receipt, external collection, and mobile layout with no JS errors')
   browser.close()
 finally:
  proc.terminate();proc.wait(timeout=5);tmp.cleanup()
if __name__=='__main__':run()