#!/usr/bin/env bash
# Fail if the gateway's API contract has drifted from the core service.
#   1. gateway/openapi.json must equal what core generates now.
#   2. gateway/src/generated/core.ts must equal what openapi-typescript generates from it.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

(cd "$root/core" && uv run agent openapi --out "$tmp/openapi.json")
if ! diff -u "$root/gateway/openapi.json" "$tmp/openapi.json"; then
  echo "gateway/openapi.json is stale. Run: (cd core && uv run agent openapi --out ../gateway/openapi.json)" >&2
  exit 1
fi

(cd "$root/gateway" && npx --no-install openapi-typescript openapi.json -o "$tmp/core.ts" >/dev/null)
if ! diff -u "$root/gateway/src/generated/core.ts" "$tmp/core.ts"; then
  echo "gateway/src/generated/core.ts is stale. Run: (cd gateway && npm run gen:types)" >&2
  exit 1
fi
echo "contract in sync"
