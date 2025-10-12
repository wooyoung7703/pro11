#!/usr/bin/env sh
set -e

# Simple package.json hash to detect dependency changes
PKG_HASH_FILE=node_modules/.pkg_hash
CURRENT_HASH="$(sha256sum package.json | awk '{print $1}' 2>/dev/null || echo '')"
SAVED_HASH="$(cat "$PKG_HASH_FILE" 2>/dev/null || echo '')"

need_install=false

# 1. Marker file logic (first install)
if [ ! -f node_modules/.deps_ok ]; then
  echo "[dev-entry] deps_ok marker missing -> install required"
  need_install=true
fi

# 2. Critical packages existence
for pkg in tailwindcss lightweight-charts msw; do
  if [ ! -f "node_modules/$pkg/package.json" ]; then
    echo "[dev-entry] Missing package $pkg -> install required"
    need_install=true
  fi
done

# 3. Hash difference
if [ "$CURRENT_HASH" != "$SAVED_HASH" ]; then
  echo "[dev-entry] package.json changed (hash diff) -> install required"
  need_install=true
fi

if [ "$need_install" = true ]; then
  echo "[dev-entry] Installing frontend dependencies (primary)..."
  # Ensure devDependencies are included for Vite build (msw is required at runtime in dev)
  if ! npm install --include=dev; then
    echo "[dev-entry] Primary install failed, retrying with --legacy-peer-deps"
    npm install --include=dev --legacy-peer-deps
  fi
  echo "$CURRENT_HASH" > "$PKG_HASH_FILE"
  touch node_modules/.deps_ok
else
  echo "[dev-entry] Dependencies intact (hash=$CURRENT_HASH)"
fi

echo "[dev-entry] Starting Vite dev server"
exec npm run dev
