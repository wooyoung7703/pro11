#!/usr/bin/env bash
# Quick safety check before running services
set -euo pipefail

fail=false

REPO_ROOT="$(CDPATH= cd -- "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Safe key extraction from dotenv-style files (no eval/source)
get_key() {
  # usage: get_key KEY [files...]
  local key="$1"; shift || true
  local files=("$@")
  local val=""
  for f in "${files[@]}"; do
    if [ -f "$f" ]; then
      # Ignore comments and export syntax, capture KEY=value with optional spaces
      # shellcheck disable=SC2002
      val=$(cat "$f" | sed -n -E "s/^ *${key} *= *([^#\r\n]+).*/\\1/p" | tail -n1 | tr -d '"' | tr -d '\r')
      if [ -n "$val" ]; then echo "$val"; return 0; fi
    fi
  done
  return 1
}

file_has() {
  # usage: file_has <regex> <files...>
  local pattern="$1"; shift || true
  for f in "$@"; do
    if [ -f "$f" ] && grep -E "$pattern" "$f" >/dev/null 2>&1; then
      return 0
    fi
  done
  return 1
}

# Return 0 if value looks like a real secret (non-empty and not a known placeholder)
is_real_secret() {
  local v="${1:-}"
  [ -n "$v" ] && [ "$v" != "__SAFE__" ] && [ "$v" != "changeme" ]
}

# Resolve a key from env, then .env.private, then .env/.env.safety. Sets <prefix>_VAL and <prefix>_SRC
get_key_with_source() {
  # usage: get_key_with_source KEY PREFIX
  local key="$1"; local prefix="$2"
  local val="" src="none"

  # from environment
  if [ -n "${!key:-}" ]; then
    val="${!key}"
    src="env"
  fi
  # from .env.private
  if [ -z "$val" ]; then
    val=$(get_key "$key" .env.private "$REPO_ROOT/.env.private" || true)
    [ -n "$val" ] && src="private"
  fi
  # from .env / .env.safety
  if [ -z "$val" ]; then
    val=$(get_key "$key" .env .env.safety "$REPO_ROOT/.env" "$REPO_ROOT/.env.safety" || true)
    [ -n "$val" ] && src="envfile"
  fi

  # export vars
  eval "${prefix}_VAL=\"$val\""
  eval "${prefix}_SRC=\"$src\""
}

chk() {
  name="$1"; shift
  if eval "$@"; then
    echo "[OK] $name"
  else
    echo "[WARN] $name"
    fail=true
  fi
}

# Ensure not using real trading in local/dev by default
get_key_with_source EXCHANGE_TRADING_ENABLED ETR
chk "EXCHANGE_TRADING_ENABLED=false" "[ \"$ETR_VAL\" = false ]"

get_key_with_source BINANCE_TESTNET BT
get_key_with_source EXCHANGE EX
chk "BINANCE_TESTNET=true or EXCHANGE=mock" "[ \"$BT_VAL\" = true ] || [ \"$EX_VAL\" = mock ]"

# Disallow dangerous combo: real trading enabled on binance mainnet
chk "No real trading on binance mainnet" "! { [ \"$ETR_VAL\" = true ] && [ \"$EX_VAL\" = binance ] && [ \"$BT_VAL\" = false ]; }"

# Admin no-auth must be disabled in prod
get_key_with_source APP_ENV APPENV
APP_ENV_VAL="$APPENV_VAL"
get_key_with_source ALLOW_ADMIN_NOAUTH NOAUTH
chk "ALLOW_ADMIN_NOAUTH disabled in prod" "! { [ \"$APP_ENV_VAL\" = prod ] && [ \"$NOAUTH_VAL\" = 1 ]; }"

# Secrets hygiene: ensure prod has real API keys and non-prod avoids real keys in .env
get_key_with_source BINANCE_API_KEY BAK
get_key_with_source BINANCE_API_SECRET BAS

# In production, real keys must be present (not placeholders)
chk "Prod requires real Binance API keys" "[ \"$APP_ENV_VAL\" != prod ] || { is_real_secret \"$BAK_VAL\" && is_real_secret \"$BAS_VAL\"; }"

# In non-prod, discourage storing real secrets in tracked .env files
chk "Non-prod: avoid real keys in .env" "[ \"$APP_ENV_VAL\" = prod ] || ! { { [ \"$BAK_SRC\" = envfile ] && is_real_secret \"$BAK_VAL\"; } || { [ \"$BAS_SRC\" = envfile ] && is_real_secret \"$BAS_VAL\"; }; }"

# Scale-in parameter sanity (professional defaults)
get_key_with_source ALLOW_SCALE_IN ALLOWSI
get_key_with_source SCALE_IN_SIZE_RATIO SIR
get_key_with_source MAX_SCALEINS MSI
get_key_with_source MIN_ADD_DISTANCE_BPS MADB
get_key_with_source SCALE_IN_COOLDOWN_SEC SICS

# When scale-in is enabled, enforce reasonable ranges
chk "Scale-in ratio in (0,1] when enabled" "! { [ \"${ALLOWSI_VAL:-}\" = true ] && { [ -z \"${SIR_VAL:-}\" ] || ! awk 'BEGIN{v='\"${SIR_VAL:-0}\"'; exit !(v>0 && v<=1)}'; }; }"
chk "Scale-in max legs 1..20 when enabled" "! { [ \"${ALLOWSI_VAL:-}\" = true ] && { [ -z \"${MSI_VAL:-}\" ] || [ \"${MSI_VAL:-0}\" -lt 1 ] || [ \"${MSI_VAL:-0}\" -gt 20 ]; }; }"
chk "Min add distance bps >=10 when enabled" "! { [ \"${ALLOWSI_VAL:-}\" = true ] && { [ -z \"${MADB_VAL:-}\" ] || [ \"${MADB_VAL:-0}\" -lt 10 ]; }; }"
chk "Scale-in cooldown >=5s when enabled" "! { [ \"${ALLOWSI_VAL:-}\" = true ] && { [ -z \"${SICS_VAL:-}\" ] || [ \"${SICS_VAL:-0}\" -lt 5 ]; }; }"

# Auto-labeler sanity checks (protect DB/CPU)
get_key_with_source AUTO_LABELER_ENABLED ALEN
AL_EN="$ALEN_VAL"
get_key_with_source AUTO_LABELER_INTERVAL ALINT
AL_INT="$ALINT_VAL"
get_key_with_source AUTO_LABELER_MIN_AGE_SECONDS ALAGE
AL_MIN_AGE="$ALAGE_VAL"
get_key_with_source AUTO_LABELER_BATCH_LIMIT ALBATCH
AL_BATCH="$ALBATCH_VAL"

# When enabled: interval>=10s, min_age>=60s, batch<=5000
chk "AUTO_LABELER sanity (interval>=10,min_age>=60,batch<=5000)" "! [ \"$AL_EN\" = true ] || { [ \"${AL_INT:-0}\" -ge 10 ] && [ \"${AL_MIN_AGE:-0}\" -ge 60 ] && [ \"${AL_BATCH:-0}\" -le 5000 ]; }"
chk ".env not tracked" "command -v git >/dev/null 2>&1 && [ -d .git ] && git check-ignore -q .env || { echo '[INFO] skipping .env tracked check (git not available)'; true; }"

printf "\nResolved config (key subset):\n"
printf "  APP_ENV=%s (src=%s)\n" "$APP_ENV_VAL" "${APPENV_SRC:-none}"
printf "  EXCHANGE=%s (src=%s), EXCHANGE_TRADING_ENABLED=%s (src=%s), BINANCE_TESTNET=%s (src=%s)\n" "${EX_VAL:-}" "${EX_SRC:-none}" "${ETR_VAL:-}" "${ETR_SRC:-none}" "${BT_VAL:-}" "${BT_SRC:-none}"
printf "  ALLOW_ADMIN_NOAUTH=%s (src=%s)\n" "${NOAUTH_VAL:-}" "${NOAUTH_SRC:-none}"
printf "  AUTO_LABELER_ENABLED=%s (src=%s), INTERVAL=%s (src=%s), MIN_AGE=%s (src=%s), BATCH_LIMIT=%s (src=%s)\n" "${AL_EN:-}" "${ALEN_SRC:-none}" "${AL_INT:-}" "${ALINT_SRC:-none}" "${AL_MIN_AGE:-}" "${ALAGE_SRC:-none}" "${AL_BATCH:-}" "${ALBATCH_SRC:-none}"
printf "  BINANCE_API_KEY src=%s, BINANCE_API_SECRET src=%s\n" "${BAK_SRC:-none}" "${BAS_SRC:-none}"
printf "  SCALE_IN allow=%s (src=%s), ratio=%s (src=%s), max_legs=%s (src=%s), min_bps=%s (src=%s), cooldown=%s (src=%s)\n" \
  "${ALLOWSI_VAL:-}" "${ALLOWSI_SRC:-none}" "${SIR_VAL:-}" "${SIR_SRC:-none}" "${MSI_VAL:-}" "${MSI_SRC:-none}" "${MADB_VAL:-}" "${MADB_SRC:-none}" "${SICS_VAL:-}" "${SICS_SRC:-none}"

if $fail; then
  printf "\nSome safety checks failed. Suggested fixes:\n"
  [ "$ETR_VAL" != false ] && printf "  - Set EXCHANGE_TRADING_ENABLED=false (or ensure it is not true)\n"
  if [ "$BT_VAL" != true ] && [ "$EX_VAL" != mock ]; then
    printf "  - Set BINANCE_TESTNET=true or EXCHANGE=mock\n"
  fi
  if [ "$ETR_VAL" = true ] && [ "$EX_VAL" = binance ] && [ "$BT_VAL" = false ]; then
    printf "  - Do NOT enable real trading on binance mainnet (set BINANCE_TESTNET=true or disable trading)\n"
  fi
  # Tailored guidance when env vars override safe .env defaults
  if [ "${ETR_SRC:-}" = env ]; then
    printf "  - EXCHANGE_TRADING_ENABLED is set via environment. Unset or set to false in your env for local/dev.\n"
  fi
  if [ "${BT_SRC:-}" = env ] && [ "$BT_VAL" != true ]; then
    printf "  - BINANCE_TESTNET is set via environment. Set to true (or EXCHANGE=mock) for local/dev.\n"
  fi
  if [ "${EX_SRC:-}" = env ] && [ "$EX_VAL" != mock ] && [ "$BT_VAL" != true ]; then
    printf "  - EXCHANGE is set via environment. Use EXCHANGE=mock or enable testnet for safe local runs.\n"
  fi
  if [ "${ALEN_SRC:-}" = env ] || [ "${ALINT_SRC:-}" = env ] || [ "${ALAGE_SRC:-}" = env ] || [ "${ALBATCH_SRC:-}" = env ]; then
    printf "  - AUTO_LABELER_* comes from environment. Ensure INTERVAL>=10, MIN_AGE>=60, BATCH_LIMIT<=5000.\n"
  fi
  if [ "$APP_ENV_VAL" = prod ] && [ "$NOAUTH_VAL" = 1 ]; then
    printf "  - Disable ALLOW_ADMIN_NOAUTH in production (set to 0)\n"
  fi
  if [ "$APP_ENV_VAL" = prod ]; then
    if ! is_real_secret "${BAK_VAL:-}" || ! is_real_secret "${BAS_VAL:-}"; then
      printf "  - In prod, set BINANCE_API_KEY and BINANCE_API_SECRET to real values (env or .env.private). Avoid '__SAFE__' placeholders.\n"
    fi
  else
    if { [ "${BAK_SRC:-}" = envfile ] && is_real_secret "${BAK_VAL:-}"; } || { [ "${BAS_SRC:-}" = envfile ] && is_real_secret "${BAS_VAL:-}"; }; then
      printf "  - Move real API keys out of .env (use .env.private or environment variables) for non-prod.\n"
    fi
  fi
  if [ "$AL_EN" = true ]; then
    if [ "${AL_INT:-0}" -lt 10 ] || [ "${AL_MIN_AGE:-0}" -lt 60 ] || [ "${AL_BATCH:-0}" -gt 5000 ]; then
      printf "  - Adjust AUTO_LABELER_* to: INTERVAL>=10, MIN_AGE>=60, BATCH_LIMIT<=5000\n"
    fi
  fi
  if [ "${ALLOWSI_VAL:-}" = true ]; then
    # ratio check via awk for float
    if [ -z "${SIR_VAL:-}" ] || ! awk 'BEGIN{v='"${SIR_VAL:-0}"'; exit (v>0 && v<=1)?0:1}'; then
      printf "  - Set SCALE_IN_SIZE_RATIO to (0,1] (e.g., 0.5).\n"
    fi
    if [ -z "${MSI_VAL:-}" ] || [ "${MSI_VAL:-0}" -lt 1 ] || [ "${MSI_VAL:-0}" -gt 20 ]; then
      printf "  - Set MAX_SCALEINS between 1 and 20 (e.g., 10).\n"
    fi
    if [ -z "${MADB_VAL:-}" ] || [ "${MADB_VAL:-0}" -lt 10 ]; then
      printf "  - Set MIN_ADD_DISTANCE_BPS >= 10 (e.g., 25 = 0.25%%).\n"
    fi
    if [ -z "${SICS_VAL:-}" ] || [ "${SICS_VAL:-0}" -lt 5 ]; then
      printf "  - Set SCALE_IN_COOLDOWN_SEC >= 5 (e.g., 30).\n"
    fi
  fi
  printf "\nReview .env or use .env.safety for local runs.\n"
  exit 1
else
  printf "\nAll safety checks passed.\n"
fi
