# Integration provider connectors (GSC / GA4 / Bing).
#
# ``oauth`` owns the per-transport OAuth machinery (code exchange, refresh,
# revoke, and the cheap authenticated grant probe) over httpx with an
# injected-transport test seam. Provider data-API clients (``gsc``/``ga4``/
# ``bing``) land with the sync pipeline tasks.
