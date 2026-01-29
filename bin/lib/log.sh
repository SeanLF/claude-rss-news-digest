# Shared logging helpers for bin/* scripts
# Usage: source "$(dirname "$0")/lib/log.sh"

BLUE='\033[0;34m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
BOLD='\033[1m'
RESET='\033[0m'

log_stage() {
  echo ""
  echo -e "${BOLD}${BLUE}═══════════════════════════════════════════════${RESET}"
  echo -e "${BOLD}${BLUE}  $1${RESET}"
  echo -e "${BOLD}${BLUE}═══════════════════════════════════════════════${RESET}"
}

log_step()    { echo -e "${GREEN}→${RESET} $1"; }
log_info()    { echo -e "${YELLOW}  $1${RESET}"; }
log_error()   { echo -e "${RED}✗ $1${RESET}" >&2; }
log_success() { echo -e "${GREEN}✓ $1${RESET}"; }
