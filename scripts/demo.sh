#!/usr/bin/env bash
# End-to-end REACT Merchant demo against a running API (default http://localhost:8000).
# DETECT -> INVESTIGATE -> QUANTIFY (INR) -> RECOMMEND -> HUMAN APPROVAL -> SIMULATED ACTION -> AUDIT.
# Everything is synthetic and every action is simulated: no order, price, message or payment leaves REACT.
set -euo pipefail
BASE="${BASE:-http://localhost:8000}"
SCENARIO="${1:-stockout_crisis}"
j() { python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps($1, indent=2, default=str))"; }
post() { curl -fsS -X POST "$BASE$1" -H 'content-type: application/json' -d "$2"; }

echo "== health";        curl -fsS "$BASE/health" | j 'd'
echo "== merchant";      curl -fsS "$BASE/merchant" | j '{k: d[k] for k in ("business_name", "city", "data_window") if k in d}'
echo "== scenario $SCENARIO (rebuild the simulated business, then scan)"
DEMO=$(post /demo/run "{\"scenario\": \"$SCENARIO\"}")
echo "$DEMO" | j '{"detected": d["detected"], "featured": d["featured"]}'
KIND=$(echo "$DEMO" | python3 -c "import sys,json; print(json.load(sys.stdin)['featured']['kind'])")
ID=$(echo "$DEMO" | python3 -c "import sys,json; print(json.load(sys.stdin)['featured']['id'])")
echo "== investigate + recommend"
DEC=$(post /react/run "{\"${KIND}_id\": \"$ID\"}")
echo "$DEC" | j '{k: d[k] for k in ("status", "hypothesis_label", "evidence_support_score", "action_type", "target_name", "summary")}'
DID=$(echo "$DEC" | python3 -c "import sys,json; print(json.load(sys.stdin)['decision_id'])")
echo "== approve (human) -> simulated action"
post "/decisions/$DID/approve" '{"reviewed_by": "Store owner", "execute": true}' | j '{"status": d["status"], "result": d["actions"][0]["result"]}'
echo "== evidence pack";  curl -fsS "$BASE/decisions/$DID/artifacts" | j '[(a["status"], a["sha256"]) for a in d]'
