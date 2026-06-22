const CACHE_NAME = 'mc-komut-v2';
const urlsToCache = [
  '/',
  '/manifest.json',
  '/icon.png',
  '/logo.png',
  '/icons/icon-192x192.png',
  '/icons/icon-512x512.png'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(urlsToCache);
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames
          .filter(name => name !== CACHE_NAME)
          .map(name => caches.delete(name))
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  // Sadece GET isteklerini cache'le
  if (event.request.method !== 'GET') return;
  
  event.respondWith(
    caches.match(event.request).then(response => {
      if (response) return response;
      return fetch(event.request).then(fetchResponse => {
        // Geçerli response'ları cache'e ekle
        if (fetchResponse && fetchResponse.status === 200 && fetchResponse.type === 'basic') {
          const responseToCache = fetchResponse.clone();
          caches.open(CACHE_NAME).then(cache => {
            cache.put(event.request, responseToCache);
          });
        }
        return fetchResponse;
      });
    }).catch(() => {
      // Offline fallback
      return caches.match('/');
    })
  );
});
