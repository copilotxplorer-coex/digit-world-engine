# Read Worker Feature Flag M3

Additive over v035+Foundation+M1+M2+HTTP body fix (242tests).
ReadWorld subclass integrates actual authenticated game look/nearby request path.
Default OFF, no service started. ON uses bounded async prefetch + verified cache;
miss/stale/unavailable/busy/oversize -> ordinary Central response, no Worker wait
under World lock. Cache exact geometry hash+monotonic TTL5s, membership checked
against legacy hypot before hydration. Self/schema/order/session preserved.
inspect/say/mutations untouched. Original runtime files/NodeLauncher unchanged.
Entry: python -m gameplay_worker.read_server --enable-read-worker (temporary DB).
Demo: python -m gameplay_worker.read_demo (actual gameHTTP/Tick/subprocessWorker).
23new tests; total265. Linux tested, Windows pending. No production deploy,
no rewards, no performance claim. Publisher currently excludes these modules;
do not publish feature until packaging/versioning is designed. Read docs first.
