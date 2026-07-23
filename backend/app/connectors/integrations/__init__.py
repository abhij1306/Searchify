# Integration provider connectors (GSC / GA4 / Bing).
#
# ``oauth`` owns the per-transport OAuth machinery (code exchange, refresh,
# revoke, and the cheap authenticated grant probe for Google grants) over
# httpx with an injected-transport test seam. The provider data-API clients
# (``gsc`` / ``ga4`` / ``bing``) page the provider stats endpoints behind
# the sync worker through the config-owned dispatch registry
# (``INTEGRATION_CLIENT_BUILDERS``); ``bing`` additionally owns the cheap
# authenticated probe for Microsoft grants (``GetSites``).
