# Integrations domain: OAuth connect flow + connection management services.
#
# ``service`` owns the section-2 connect flow (state mint/consume, code
# exchange, grant find-or-create, connection attach) and the section-5
# management surface (list / test / delete). ``schemas`` owns the DTOs —
# which NEVER carry tokens (invariant 6). ``mappings`` owns the
# property-mapping write-time validation + lifecycle (spec section 3, I8),
# ``derive`` the derivation pass that resolves mapping rows into fact rows
# (I9), and ``sync`` the sync-run bookkeeping shared by the workers.
