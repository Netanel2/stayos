// STAYOS v3 service worker: installability only. Do NOT cache private guest/API data.
self.addEventListener('install',event=>{self.skipWaiting();});
self.addEventListener('activate',event=>{event.waitUntil(self.clients.claim());});
