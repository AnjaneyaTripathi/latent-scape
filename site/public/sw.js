const BUILD_FINGERPRINT = '__BUILD_FINGERPRINT__';
let cacheName = `latentscape-static-${BUILD_FINGERPRINT}`;
const CORE = ['./', './index.html', './data/manifest.json', './data/search.json', './data/topics.json'];
const currentCache = async () => cacheName;
self.addEventListener('install', (event) => event.waitUntil(currentCache().then((name) => caches.open(name)).then((cache) => cache.addAll(CORE)).then(() => self.skipWaiting())));
self.addEventListener('activate', (event) => event.waitUntil(currentCache().then((name) => Promise.all([self.clients.claim(), caches.keys().then((keys) => Promise.all(keys.filter((key) => key.startsWith('latentscape-static-') && key !== name).map((key) => caches.delete(key))))]))));
self.addEventListener('message', (event) => {
  if (event.data?.type !== 'precache' || !Array.isArray(event.data.urls)) return;
  event.waitUntil(currentCache().then((name) => caches.open(name)).then((cache) => Promise.all(event.data.urls.map((url) => cache.add(url).catch(() => undefined)))));
});
self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET' || new URL(event.request.url).origin !== self.location.origin) return;
  event.respondWith(caches.match(event.request).then((cached) => cached || fetch(event.request).then((response) => { if (response.ok) { const copy = response.clone(); void currentCache().then((name) => caches.open(name)).then((cache) => cache.put(event.request, copy)); } return response; })));
});
