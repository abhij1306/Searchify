#!/usr/bin/env bash
# Seed script for abhij1306/Searchify.
#
# Creates realistic demo data end-to-end:
#   - 1 demo user (demo@searchify.dev / DemoPass123!) + personal workspace
#     (created via the register API so password hashing/session-cookie flow
#     is exercised exactly as the app does it)
#   - 1 project ("Acme Running Shoes", brand "Acme", 3 competitors, owned +
#     unintended domains) created via the API
#   - 1 prompt set with prompts covering every intent
#     (discovery/comparison/purchase/service/local/unspecified)
#   - 3 BYOK provider connections (openai/anthropic/google) with FAKE keys
#     (no real provider calls are made in this sandbox -- see known-issues.md)
#   - 4 audits covering the full lifecycle status spectrum:
#       - completed            (with tasks/artifacts/analyses/metric snapshot)
#       - partially_completed  (some succeeded, some failed)
#       - failed               (all tasks failed)
#       - running              (still in-flight, no results yet)
#     Because we don't have real OpenAI/Anthropic/Google API keys, the
#     completed/partially_completed/failed audits are seeded directly at the
#     ORM level with fabricated (but realistic) LLM answers/citations/scores
#     rather than run through the live worker.
#
# Idempotent: re-running this script is safe. The demo user/project/prompts
# are looked up by unique key (email / project name) and reused if they
# already exist; audits are only created once per label (checked by a
# marker in Audit.error_message for the failed one / by counting existing
# audits for the project).
#
# Prerequisites:
#   - Postgres running and migrated (see setup-instructions.md)
#   - Backend running on http://localhost:8000 (for the register/project/
#     prompt-set/provider-connection API calls)
#   - backend/.env present with a working DATABASE_URL (used by the Python
#     seeding script for direct ORM writes of audit data)
#
# Usage:
#   bash testing/local-stack/seed.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
BACKEND_DIR="$REPO_DIR/backend"
API_BASE="${SEARCHIFY_API_BASE:-http://localhost:3000}"   # via Next.js proxy
DIRECT_BACKEND="${SEARCHIFY_BACKEND_BASE:-http://localhost:8000}"

echo "=== Searchify seed: checking backend health ==="
curl -sf "$DIRECT_BACKEND/health" >/dev/null || {
  echo "ERROR: backend not reachable at $DIRECT_BACKEND/health -- start it first." >&2
  exit 1
}

COOKIE_JAR="$(mktemp)"
trap 'rm -f "$COOKIE_JAR"' EXIT

EMAIL="demo@searchify.dev"
PASSWORD="DemoPass123!"

echo "=== Registering (or logging in) demo user: $EMAIL ==="
REGISTER_HTTP_CODE=$(curl -s -o /tmp/searchify_register.json -w '%{http_code}' \
  -c "$COOKIE_JAR" \
  -X POST "$API_BASE/api/v1/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\",\"name\":\"Demo User\"}")

if [ "$REGISTER_HTTP_CODE" = "201" ]; then
  echo "Registered new user $EMAIL"
elif [ "$REGISTER_HTTP_CODE" = "409" ] || [ "$REGISTER_HTTP_CODE" = "400" ]; then
  echo "User already exists ($REGISTER_HTTP_CODE) -- logging in instead"
  curl -s -o /tmp/searchify_login.json -w '%{http_code}\n' \
    -c "$COOKIE_JAR" \
    -X POST "$API_BASE/api/v1/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}"
else
  echo "WARNING: unexpected register status $REGISTER_HTTP_CODE" >&2
  cat /tmp/searchify_register.json >&2 || true
fi

echo "=== Fetching current user / workspace ==="
curl -s -b "$COOKIE_JAR" "$API_BASE/api/v1/auth/me" -o /tmp/searchify_me.json
cat /tmp/searchify_me.json
echo

echo "=== Creating (or reusing) project 'Acme Running Shoes' ==="
EXISTING_PROJECT_ID=$(curl -s -b "$COOKIE_JAR" "$API_BASE/api/v1/projects" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); items=d if isinstance(d,list) else d.get('items',d.get('projects',[])); print(next((p['id'] for p in items if p.get('name')=='Acme Running Shoes'), ''))" 2>/dev/null || echo "")

if [ -n "$EXISTING_PROJECT_ID" ]; then
  PROJECT_ID="$EXISTING_PROJECT_ID"
  echo "Reusing existing project $PROJECT_ID"
else
  PROJECT_JSON=$(curl -s -b "$COOKIE_JAR" -X POST "$API_BASE/api/v1/projects" \
    -H "Content-Type: application/json" \
    -d '{
      "name": "Acme Running Shoes",
      "brand_name": "Acme",
      "brand": {"aliases": ["Acme Footwear", "Acme Running"]},
      "website_url": "https://acme-running.example.com",
      "owned_domains": ["acme-running.example.com", "blog.acme-running.example.com"],
      "unintended_domains": ["support.acme-running.example.com"],
      "competitors": [
        {"name": "Velocity Sports", "aliases": ["Velocity"], "domains": ["velocitysports.example.com"]},
        {"name": "Trailblazer Co", "aliases": ["Trailblazer"], "domains": ["trailblazer.example.com"]},
        {"name": "Nimbus Athletics", "aliases": ["Nimbus"], "domains": ["nimbusathletics.example.com"]}
      ],
      "country_code": "US",
      "language_code": "en",
      "benchmark_mode": "controlled_localized",
      "default_repetitions": 3
    }')
  PROJECT_ID=$(echo "$PROJECT_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
  echo "Created project $PROJECT_ID"
fi

echo "=== Creating (or reusing) prompt set with prompts across all intents ==="
EXISTING_SET_ID=$(curl -s -b "$COOKIE_JAR" "$API_BASE/api/v1/projects/$PROJECT_ID" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); ps=d.get('prompt_sets',[]); print(next((s['id'] for s in ps if s.get('name')=='Core Benchmark Prompts'), ''))" 2>/dev/null || echo "")

if [ -n "$EXISTING_SET_ID" ]; then
  PROMPT_SET_ID="$EXISTING_SET_ID"
  echo "Reusing existing prompt set $PROMPT_SET_ID"
else
  SET_JSON=$(curl -s -b "$COOKIE_JAR" -X POST "$API_BASE/api/v1/prompt-sets" \
    -H "Content-Type: application/json" \
    -d "{\"project_id\":\"$PROJECT_ID\",\"name\":\"Core Benchmark Prompts\",\"description\":\"Seeded prompts covering every intent\"}")
  PROMPT_SET_ID=$(echo "$SET_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
  echo "Created prompt set $PROMPT_SET_ID"
fi

EXISTING_PROMPT_COUNT=$(curl -s -b "$COOKIE_JAR" "$API_BASE/api/v1/prompt-sets/$PROMPT_SET_ID/prompts" \
  | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")

if [ "$EXISTING_PROMPT_COUNT" != "0" ]; then
  echo "Prompt set already has $EXISTING_PROMPT_COUNT prompts, skipping prompt creation"
else
  declare -a PROMPTS=(
    'What are the best running shoe brands for marathon training?|discovery|false'
    'Compare Acme running shoes to Velocity Sports running shoes.|comparison|true'
    'Where can I buy Acme running shoes online right now?|purchase|true'
    'How do I start a return for my Acme running shoes order?|service|true'
    'What running shoe stores are near downtown Chicago?|local|false'
    'Tell me about trends in athletic footwear cushioning technology.||false'
  )
  for row in "${PROMPTS[@]}"; do
    IFS='|' read -r text intent branded <<< "$row"
    curl -s -b "$COOKIE_JAR" -X POST "$API_BASE/api/v1/prompt-sets/$PROMPT_SET_ID/prompts" \
      -H "Content-Type: application/json" \
      -d "{\"text\":\"$text\",\"theme\":\"footwear\",\"intent\":\"$intent\",\"branded\":$branded,\"enabled\":true}" \
      -o /dev/null
  done
  echo "Created 6 prompts covering discovery/comparison/purchase/service/local/unspecified intents"
fi

echo "=== Creating (or reusing) BYOK provider connections (FAKE keys -- see known-issues.md) ==="
EXISTING_CONNECTIONS=$(curl -s -b "$COOKIE_JAR" "$API_BASE/api/v1/provider-connections")
for provider in openai anthropic google; do
  HAS_ONE=$(echo "$EXISTING_CONNECTIONS" | python3 -c "
import sys, json
d = json.load(sys.stdin)
items = d if isinstance(d, list) else d.get('items', d.get('connections', []))
print('yes' if any(c.get('transport_provider') == '$provider' for c in items) else 'no')
" 2>/dev/null || echo "no")
  if [ "$HAS_ONE" = "yes" ]; then
    echo "Provider connection for $provider already exists, skipping"
    continue
  fi
  case "$provider" in
    openai) route='{"logical_engine":"chatgpt","transport_provider":"openai","transport_model":"gpt-5.4"}' ;;
    anthropic) route='{"logical_engine":"claude","transport_provider":"anthropic","transport_model":"claude-sonnet-4-6"}' ;;
    google) route='{"logical_engine":"gemini","transport_provider":"google","transport_model":"gemini-flash-latest"}' ;;
  esac
  curl -s -b "$COOKIE_JAR" -X POST "$API_BASE/api/v1/provider-connections" \
    -H "Content-Type: application/json" \
    -d "{\"label\":\"Demo $provider key (fake, untested)\",\"transport_provider\":\"$provider\",\"api_key\":\"sk-fake-demo-key-not-a-real-secret-0000000000\",\"routes\":[$route]}" \
    -o /dev/null
  echo "Created $provider connection"
done

echo "=== Seeding audits across the full lifecycle (direct ORM, no live provider calls) ==="
cd "$BACKEND_DIR"
PROJECT_ID="$PROJECT_ID" uv run python "$SCRIPT_DIR/seed_audits.py"

echo "=== Seed complete ==="
echo "Demo login: $EMAIL / $PASSWORD"
echo "Project ID: $PROJECT_ID"
