# Shared logging helpers for bin/* scripts
# Usage: source "$(dirname "$0")/lib/log.sh"

BLUE='\033[0;34m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
BOLD='\033[1m'
RESET='\033[0m'

# Quiet mode: suppress verbose command output, keep status lines
QUIET=${CLAUDECODE:-}

log_stage() {
  if [[ $QUIET == "1" ]]; then
    echo -e "\n${BOLD}$1${RESET}"
  else
    echo ""
    echo -e "${BOLD}${BLUE}═══════════════════════════════════════════════${RESET}"
    echo -e "${BOLD}${BLUE}  $1${RESET}"
    echo -e "${BOLD}${BLUE}═══════════════════════════════════════════════${RESET}"
  fi
}

log_step()    { echo -e "${GREEN}→${RESET} $1"; }
log_info()    { echo -e "${YELLOW}  $1${RESET}"; }
log_error()   { echo -e "${RED}✗ $1${RESET}" >&2; }
log_success() { echo -e "${GREEN}✓ $1${RESET}"; }

# Run a command, capturing all output in quiet mode (shown only on failure).
# Uses </dev/null to fail fast if a command unexpectedly prompts for input.
run_quiet() {
  if [[ $QUIET == "1" ]]; then
    local output
    if output=$("$@" < /dev/null 2>&1); then
      return 0
    else
      local rc=$?
      echo "$output" >&2
      return $rc
    fi
  else
    "$@"
  fi
}
