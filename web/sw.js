/* Caches the app shell so the panel still opens (and can say what is wrong) when the
   PC is unreachable. Network first, because a stale shell against a newer server is
   worse than a slightly slower start. */

const CACHE = 'rigdeck-v6';
const SHELL = ['./', './index.html', './style.css', './app.js', './mapdata.js',
               './manifest.webmanifest', './icon.svg'];
const MAPS = '/maps/';   // road cells: left to the browser's own cache, see below

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const request = event.request;
  const path = new URL(request.url).pathname;
  if (request.method !== 'GET' || path.startsWith('/api/')) return;

  // The map is left entirely alone. The panel asks for cells in bursts of eight or more
  // as it drives, and routing those through here stalled them outright; the server marks
  // them cacheable for a week, so the browser's own cache keeps them anyway. Handing back
  // the panel's HTML on a miss would be worse still -- the tablet would try to parse a
  // page as road geometry.
  if (path.startsWith(MAPS)) return;

  event.respondWith(
    fetch(request)
      .then(response => {
        const copy = response.clone();
        caches.open(CACHE).then(cache => cache.put(request, copy)).catch(() => {});
        return response;
      })
      .catch(() => caches.match(request).then(hit => hit || caches.match('./index.html')))
  );
});
