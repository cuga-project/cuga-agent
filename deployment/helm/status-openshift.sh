#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# CUGA OpenShift Status Script
#
# Inspect pods, logs, routes, and events for a deployment based on openshift.env
#
# Usage:
#   ./status-openshift.sh [path/to/openshift.env] [command] [options]
#
# Commands (default: pods):
#   pods, list     List CUGA pods with status
#   all            List all deployment pods (cuga, postgres, vault)
#   describe       Describe CUGA pods
#   startup        Pod startup status and events (what's happening now)
#   logs [pod]     Show pod logs (-f to follow)
#   route          Show route URL and access links
#   events         Show recent namespace events
#   status         Helm release status + pod summary
#
# Examples:
#   ./status-openshift.sh
#   ./status-openshift.sh openshift.env logs -f
#   ./status-openshift.sh openshift.env logs cuga-cuga-agent-abc123-xyz
#   ./status-openshift.sh openshift.env describe
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE=""
CMD="pods"
LOG_FOLLOW=false
POD_NAME=""
KUBECTL_TIMEOUT="${KUBECTL_REQUEST_TIMEOUT:-120}"

KUBE=$(command -v oc 2>/dev/null || command -v kubectl 2>/dev/null || {
  echo "ERROR: oc or kubectl not found"
  exit 1
})

# Colors (no-op if not tty)
if [[ -t 1 ]]; then
  RED='\033[0;31m'
  GREEN='\033[0;32m'
  YELLOW='\033[1;33m'
  BLUE='\033[0;34m'
  CYAN='\033[0;36m'
  BOLD='\033[1m'
  NC='\033[0m'
else
  RED='' GREEN='' YELLOW='' BLUE='' CYAN='' BOLD='' NC=''
fi

eecho() { printf '%b\n' "$*"; }

print_usage() {
  echo ""
  eecho "${BOLD}Usage:${NC} $0 [path/to/openshift.env] [command] [options]"
  echo ""
  eecho "${BOLD}Commands:${NC}"
  echo "  pods, list     List CUGA pods (default)"
  echo "  all            List cuga + postgres + vault pods"
  echo "  describe       Describe CUGA pods"
  echo "  startup        Pod startup status and events (what's happening now)"
  echo "  logs [pod]     Pod logs (use -f to follow)"
  echo "  route          Show route URL and access links"
  echo "  events         Recent namespace events"
  echo "  status         Helm status + pod summary"
  echo ""
  eecho "${BOLD}Examples:${NC}"
  echo "  $0"
  echo "  $0 openshift.env logs -f"
  echo "  $0 openshift.env describe"
  echo ""
}

# Parse args: [env] [command] [options]
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) print_usage; exit 0 ;;
    -f) LOG_FOLLOW=true; shift ;;
    logs) CMD="logs"; shift ;;
    pods|list) CMD="pods"; shift ;;
    all) CMD="all"; shift ;;
    describe) CMD="describe"; shift ;;
    startup) CMD="startup"; shift ;;
    route) CMD="route"; shift ;;
    events) CMD="events"; shift ;;
    status) CMD="status"; shift ;;
    *)
      if [[ -z "$ENV_FILE" && -f "$1" ]]; then
        ENV_FILE="$1"
      elif [[ "$CMD" == "logs" && -z "$POD_NAME" && "$1" != -* ]]; then
        POD_NAME="$1"
      elif [[ -z "$ENV_FILE" ]]; then
        ENV_FILE="$1"
      else
        echo "Unknown argument: $1"
        print_usage
        exit 1
      fi
      shift
      ;;
  esac
done

ENV_FILE="${ENV_FILE:-${SCRIPT_DIR}/openshift.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  eecho "${RED}ERROR:${NC} env file not found: $ENV_FILE"
  print_usage
  exit 1
fi

# shellcheck disable=SC1090
set -a
source "$ENV_FILE"
set +a

INSTANCE_ID="${INSTANCE_ID:?INSTANCE_ID not set in $ENV_FILE}"
NAMESPACE="${NAMESPACE:-cuga}"
RELEASE_NAME="cuga-${INSTANCE_ID}"

k() {
  $KUBE "$@" --namespace "${NAMESPACE}" --request-timeout="${KUBECTL_TIMEOUT}"
}

header() {
  echo ""
  eecho "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  eecho "  ${BOLD}$1${NC}"
  eecho "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

case "$CMD" in
  pods|list)
    header "CUGA Pods (${RELEASE_NAME})"
    echo ""
    k get pods -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o wide
    echo ""
    k get pods -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o custom-columns='NAME:.metadata.name,STATUS:.status.phase,RESTARTS:.status.containerStatuses[0].restartCount,AGE:.metadata.creationTimestamp'
    ;;
  all)
    header "All Deployment Pods"
    echo ""
    eecho "${BOLD}CUGA:${NC}"
    k get pods -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o wide 2>/dev/null || echo "  (none)"
    echo ""
    eecho "${BOLD}PostgreSQL:${NC}"
    k get pods -l "app.kubernetes.io/name=postgres-pgvector" -o wide 2>/dev/null || echo "  (none)"
    echo ""
    eecho "${BOLD}Vault:${NC}"
    k get pods -l "app.kubernetes.io/name=vault" -o wide 2>/dev/null || echo "  (none)"
    ;;
  describe)
    header "Describe CUGA Pods"
    PODS=$(k get pods -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null || true)
    if [[ -z "$PODS" ]]; then
      eecho "${YELLOW}No pods found for ${RELEASE_NAME}${NC}"
      exit 0
    fi
    for p in $PODS; do
      echo ""
      k describe pod "$p"
    done
    ;;
  startup)
    header "Pod Startup Status"
    POD=$(k get pods -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
    if [[ -z "$POD" ]]; then
      eecho "${YELLOW}No CUGA pod found.${NC} Deployment may not be created yet."
      echo ""
      k get pods -l "app.kubernetes.io/instance=${RELEASE_NAME}" 2>/dev/null || true
      exit 0
    fi
    eecho "Pod: ${BOLD}${POD}${NC}"
    echo ""
    eecho "${BOLD}Current status:${NC}"
    k get pod "$POD" -o wide
    echo ""
    eecho "${BOLD}Events (what is happening now):${NC}"
    k get events --field-selector "involvedObject.name=${POD}" --sort-by='.lastTimestamp' 2>/dev/null | tail -25 || k get events --sort-by='.lastTimestamp' 2>/dev/null | tail -20
    echo ""
    eecho "${CYAN}Tip: run 'describe' for full details, 'logs -f' to follow logs${NC}"
    ;;
  logs)
    header "Pod Logs"
    if [[ -n "$POD_NAME" ]]; then
      POD="$POD_NAME"
    else
      POD=$(k get pods -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
    fi
    if [[ -z "$POD" ]]; then
      eecho "${YELLOW}No CUGA pod found.${NC} Specify pod name: $0 $ENV_FILE logs <pod-name>"
      exit 1
    fi
    eecho "Pod: ${BOLD}${POD}${NC}"
    echo ""
    if [[ "$LOG_FOLLOW" == true ]]; then
      k logs "$POD" --all-containers -f 2>&1
    else
      LOGS=$(k logs "$POD" --all-containers --tail=100 2>&1) || true
      if [[ -n "$LOGS" ]]; then
        printf '%s\n' "$LOGS"
      else
        eecho "${YELLOW}No logs yet (container may still be starting).${NC}"
      fi
      echo ""
      eecho "${CYAN}Tip: use -f to follow logs${NC}"
    fi
    ;;
  route)
    header "Route & Access"
    HOST=$(k get route "${RELEASE_NAME}" -o jsonpath='{.spec.host}' 2>/dev/null || true)
    if [[ -z "$HOST" ]]; then
      eecho "${YELLOW}Route not found.${NC} Deployment may still be starting."
      k get route -l "app.kubernetes.io/instance=${RELEASE_NAME}" 2>/dev/null || true
      exit 0
    fi
    echo ""
    eecho "  ${BOLD}Host:${NC}  https://${HOST}"
    echo ""
    eecho "  ${BOLD}Links:${NC}"
    eecho "    App     ${BLUE}https://${HOST}/${NC}"
    eecho "    Chat    ${BLUE}https://${HOST}/chat${NC}"
    eecho "    Manage  ${BLUE}https://${HOST}/manage${NC}"
    echo ""
    ;;
  events)
    header "Recent Events (${NAMESPACE})"
    echo ""
    k get events --sort-by='.lastTimestamp' | tail -30
    ;;
  status)
    header "Helm Release: ${RELEASE_NAME}"
    echo ""
    helm status "${RELEASE_NAME}" --namespace "${NAMESPACE}" 2>/dev/null || echo "Release not found"
    echo ""
    header "Pod Summary"
    k get pods -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o custom-columns='POD:.metadata.name,STATUS:.status.phase,READY:.status.containerStatuses[0].ready,RESTARTS:.status.containerStatuses[0].restartCount'
    echo ""
    HOST=$(k get route "${RELEASE_NAME}" -o jsonpath='{.spec.host}' 2>/dev/null || true)
    if [[ -n "$HOST" ]]; then
      eecho "  ${BOLD}URL:${NC} https://${HOST}"
    fi
    ;;
  *)
    echo "Unknown command: $CMD"
    print_usage
    exit 1
    ;;
esac

echo ""
