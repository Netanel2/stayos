import sys,time,requests,base64
from pathlib import Path
from playwright.sync_api import sync_playwright
from test_platform import start

ART=Path('/mnt/data/stayos-v3-screens');ART.mkdir(exist_ok=True)
HTML='<html lang="en"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="app">Loading...</div><div id="toast"></div></body></html>'
JS=Path(__file__).parents[1]/'static'/'app.js'
CSS=Path(__file__).parents[1]/'static'/'style.css'
ASSETDIR=Path(__file__).parents[1]/'static'/'assets'
ASSET_DATA={p.name:'data:image/webp;base64,'+base64.b64encode(p.read_bytes()).decode() for p in ASSETDIR.glob('*.webp') if p.stat().st_size>1000}
def inlineimgs(page):
 page.evaluate('''(images)=>{document.querySelectorAll('img').forEach(img=>{let fn=(img.getAttribute('src')||'').split('/').pop();if(images[fn])img.src=images[fn];});let art=document.querySelector('.loginart');if(art&&images['resort-hero.webp'])art.style.backgroundImage='linear-gradient(180deg,#062a2633,#09282388), url('+images['resort-hero.webp']+')';}''',ASSET_DATA)


def run():
 tmp,proc,base=start()
 try:
  with sync_playwright() as p:
   browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--disable-dev-shm-usage'])
   sessions={};errors=[]
   def load(page,width,height):
    session=requests.Session();sessions[page]=session
    def proxy(source,details):
     method=details.get('method','GET');url=details['url']
     if url.startswith('/'):url=base+url
     body=details.get('body');headers=details.get('headers',{})
     if details.get('base64'):body=base64.b64decode(body)
     resp=session.request(method,url,data=body,headers=headers,timeout=8)
     return {'status':resp.status_code,'body':resp.text,'ctype':resp.headers.get('Content-Type','application/json')}
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.expose_binding('__fetchProxy',proxy)
    page.set_viewport_size({'width':width,'height':height})
    page.set_content(HTML)
    page.add_style_tag(content=CSS.read_text())
    page.evaluate('''() => {history.replaceState = ()=>{}; const mockstore={getItem:()=>null,setItem:()=>{},removeItem:()=>{}}; Object.defineProperty(window,'localStorage',{value:mockstore}); Object.defineProperty(window,'sessionStorage',{value:mockstore}); window.fetch = async (u,o={}) => {
      let body=o.body??null, enc=false;
      if(body instanceof Blob){let arr=new Uint8Array(await body.arrayBuffer());body=btoa(Array.from(arr,x=>String.fromCharCode(x)).join(''));enc=true;}
      let r=await window.__fetchProxy({url:String(u),method:o.method||'GET',body,headers:o.headers||{},base64:enc});
      return new Response(r.body,{status:r.status,headers:{'Content-Type':r.ctype}});
    };}''')
    page.add_script_tag(content=JS.read_text())
   page=browser.new_page();load(page,1440,900)
   page.get_by_text('Welcome back.').wait_for(timeout=10000)
   print('LOGIN HEADLINE',page.locator('h1').first.inner_text());inlineimgs(page);page.screenshot(path=str(ART/'01-login-desktop.png'),full_page=True)
   page.locator('input[name="username"]').fill('owner')
   page.locator('input[name="password"]').fill('SecureOwnerPass!12345')
   page.locator('#loginForm button[type=submit]').click()
   page.get_by_text('Your Hotels').wait_for(timeout=10000)
   print('OWNER CARDS',page.locator('.hotelcard').count());inlineimgs(page);page.screenshot(path=str(ART/'02-owner-desktop.png'),full_page=True)
   page.get_by_role('button',name='Manage hotel').first.click()
   page.get_by_text('Build the experience').wait_for(timeout=7000)
   print('MANAGER',page.locator('h1').first.inner_text());inlineimgs(page);page.screenshot(path=str(ART/'03-dashboard-desktop.png'),full_page=True)
   page.locator('button[data-tab="menu"]').click();page.get_by_text('Menu Products',exact=True).wait_for()
   inlineimgs(page);page.screenshot(path=str(ART/'04-menu-desktop.png'),full_page=True)
   page.get_by_role('button',name='+ Add new').click();page.locator('input[name="name_en"]').fill('Cold Brew')
   page.locator('input[name="name_he"]').fill('קפה קר')
   page.locator('input[name="price_cents"]').fill('17.50')
   page.locator('#resourceForm button[type=submit]').click();page.get_by_text('Cold Brew').wait_for(timeout=7000)
   print('ITEM CREATED')
   page.locator('button[data-tab="staff"]').click();page.get_by_text('Team & Permissions',exact=True).wait_for()
   inlineimgs(page);page.screenshot(path=str(ART/'05-staff-desktop.png'),full_page=True)
   page.locator('button[data-tab="guests"]').click();page.get_by_text('Guest Access Passes',exact=True).wait_for()
   inlineimgs(page);page.screenshot(path=str(ART/'06-stays-desktop.png'),full_page=True)
   first=page.locator('button[data-action="showQr"]').first
   if first.count():
    first.click();page.locator('.qrcode').wait_for();inlineimgs(page);page.screenshot(path=str(ART/'07-qr-desktop.png'),full_page=True)
    link=page.locator('input[readonly]').input_value();print('QR LINK',link[:90]);page.locator('button[data-action="closeModal"]').click()
   mobile=browser.new_page(viewport={'width':390,'height':844},device_scale_factor=1,is_mobile=True,has_touch=True)
   load(mobile,390,844)
   mobile.evaluate("async () => {state.slug='bayview-demo';state.preview=true;await loadPublic();renderGuest();}")
   mobile.locator('.guest-header').wait_for(timeout=7000)
   print('MOBILE GUEST TITLE',mobile.locator('h1').first.inner_text())
   inlineimgs(mobile);mobile.screenshot(path=str(ART/'08-guest-mobile.png'),full_page=True)
   mobile.locator('button[data-tab="dining"]').last.click();mobile.locator('#deliveryArea').wait_for()
   inlineimgs(mobile);mobile.screenshot(path=str(ART/'09-dining-mobile.png'),full_page=True)
   page.set_viewport_size({'width':390,'height':844})
   page.locator('button[data-tab="rooms"]').click();page.get_by_text('Physical Rooms',exact=True).wait_for()
   inlineimgs(page);page.screenshot(path=str(ART/'10-manager-mobile.png'),full_page=True)
   print('BODY OVERFLOW',page.evaluate('document.documentElement.scrollWidth > window.innerWidth'))
   print('BROWSER ERRORS',errors)
   assert not errors,errors
   browser.close()
 finally:
  proc.terminate();proc.wait(timeout=5);tmp.cleanup()
if __name__=='__main__':run()
