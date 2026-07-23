# Integrations domain: OAuth connect flow + connection management services.
#
# ``service`` owns the section-2 connect flow (state mint/consume, code
# exchange, grant find-or-create, connection attach) and the section-5
# management surface (list / test / delete). ``schemas`` owns the DTOs —
# which NEVER carry tokens (invariant 6).
