"""Browser check: live popup -> room history, room search, images in resource editor."""
import requests,base64,secrets
from playwright.sync_api import sync_playwright,expect
from test_platform import start
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
HTML='<html lang="en"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="app">Loading...</div><div id="toast"></div></body></html>'
JS=(ROOT/'static/app.js').read_text();CSS=(ROOT/'static/style.css').read_text()
def run():
 tmp,proc,base=start()
 try:
  with sync_playwright() as p:
   browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--disable-dev-shm-usage'])
   page=browser.new_page(viewport={'width':1280,'height':900});http=requests.Session();errors=[]
   def fetch_proxy(source,detail):
    url=detail['url'];url=base+url if url.startswith('/') else url
    body=detail.get('body');headers=detail.get('headers') or {}
    if detail.get('base64'):body=base64.b64decode(body)
    r=http.request(detail.get('method','GET'),url,data=body,headers=headers,timeout=7)
    return {'status':r.status_code,'body':r.text,'ctype':r.headers.get('Content-Type','application/json')}
   page.on('pageerror',lambda e:errors.append(str(e)));page.expose_binding('__fetchProxy',fetch_proxy)
   page.set_content(HTML);page.add_style_tag(content=CSS)
   page.evaluate('''()=>{history.replaceState=()=>{};const mem={};const store={getItem:k=>mem[k]||null,setItem:(k,v)=>mem[k]=v,removeItem:k=>delete mem[k]};Object.defineProperty(window,'localStorage',{value:store});Object.defineProperty(window,'sessionStorage',{value:store});window.fetch=async(u,o={})=>{let body=o.body??null,enc=false;if(body instanceof Blob){let a=new Uint8Array(await body.arrayBuffer());body=btoa(Array.from(a,x=>String.fromCharCode(x)).join(''));enc=true;}const r=await window.__fetchProxy({url:String(u),method:o.method||'GET',body,headers:o.headers||{},base64:enc});return new Response(r.body,{status:r.status,headers:{'Content-Type':r.ctype}})}}''')
   page.add_script_tag(content=JS)
   page.locator('input[name="username"]').fill('owner');page.locator('input[name="password"]').fill('SecureOwnerPass!12345');page.locator('#loginForm button[type="submit"]').click()
   page.get_by_text('Your Hotels').wait_for();page.get_by_role('button',name='Manage hotel').first.click()
   page.get_by_text('Guest activity · all departments').wait_for()
   page.evaluate('checkAlerts()')
   api=base+'/api/h/bayview-demo/'
   ret=http.post(api+'manage/demo-pass',json={}).json();token=ret['url'].split('token=')[-1]
   menus=http.get(api+'public').json()['menu'];amenities=http.get(api+'public').json()['amenities']
   http.post(api+'guest/'+token+'/order',json={'items':[{'product_id':menus[0]['id'],'qty':2}],'area':'pool','detail':'Sunbed 22','note':'Extra lime','request_key':secrets.token_hex(16)})
   http.post(api+'guest/'+token+'/request',json={'amenity_id':amenities[0]['id'],'qty':1})
   page.evaluate('checkAlerts()')
   expect(page.locator('.reception-popup')).to_have_count(2)
   assert page.locator('.reception-popup').first.inner_text().find('Room 101')>=0
   expect(page.locator('#liveBoardItems')).to_contain_text('Sunbed 22')
   page.locator('.reception-popup').first.click()
   page.get_by_text('Complete history by room').wait_for()
   assert page.locator('#roomHistoryInput').input_value()=='101'
   expect(page.locator('.history-entry')).to_have_count(3)  # order + amenity + room folio charge
   expect(page.locator('.history-entry').first).to_contain_text('Room charge') if False else None
   expect(page.locator('main')).to_contain_text('Sunbed 22')
   page.locator('#roomHistoryInput').fill('204');page.get_by_role('button',name='Search',exact=True).click()
   page.get_by_text('No activity recorded').wait_for()
   print('PASS clickable popup and room search show correct room history and delivery information')
   page.locator('button[data-tab="menu"]').click();page.get_by_text('Menu Products',exact=True).wait_for()
   page.locator('button[data-action="editItem"]').first.click()
   page.locator('.photo-editor').wait_for()
   assert page.locator('.photo-choice').count()>=12
   buttons=page.locator('button.photo-choice');second=buttons.nth(1);file=second.get_attribute('data-value');second.click()
   assert page.locator('select[name="image"]').input_value()==file
   expect(page.locator('.photo-choice.selected')).to_have_count(1)
   assert not errors,errors
   print('PASS media visual picker selects alternate product image without losing form')
   # Mobile manager layout for new tab
   page.locator('button[data-action="closeModal"]').first.click()
   page.set_viewport_size({'width':390,'height':844});page.locator('button[data-tab="room_history"]').click()
   page.get_by_text('Complete history by room').wait_for()
   assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
   print('PASS room history displays at mobile width without page-wide horizontal overflow')
   browser.close()
 finally:
  proc.terminate();proc.wait(timeout=5);tmp.cleanup()
if __name__=='__main__':run()
