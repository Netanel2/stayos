const VERSION='stayos-v21-static';const ASSETS=['/','/index.html','/style.css?v=2.1','/app.js?v=2.1','/assets/resort-hero.webp','/assets/app-icon-192.png','/assets/app-icon-512.png'];
self.addEventListener('install', event=>{event.waitUntil(caches.open(VERSION).then(c=>c.addAll(ASSETS)).then(()=>self.skipWaiting()))});
self.addEventListener('activate', event=>{event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==VERSION).map(k=>caches.delete(k)))).then(()=>self.clients.claim()))});
self.addEventListener('fetch', event=>{const req=event.request;const url=new URL(req.url);if(req.method!=='GET'||url.origin!==location.origin||url.pathname.startsWith('/api/'))return;
if(req.mode==='navigate'){event.respondWith(fetch(req).catch(()=>caches.match('/index.html')));return;}
event.respondWith(caches.match(req).then(cached=>cached||fetch(req).then(res=>{if(res.ok)caches.open(VERSION).then(c=>c.put(req,res.clone()));return res})))})
