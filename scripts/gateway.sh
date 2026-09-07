#!/usr/bin/env bash
# Home inference stack: Ollama + ngram.inference_gateway + cloudflared tunnel.
# Parity with scripts/gateway.ps1 (macOS / Linux). Invoked via: ngram gateway <on|off|status|restart>
set -euo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]}"
SCRIPT_ROOT="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_ROOT")"

if [[ $# -ge 1 ]]; then
  ACTION="$1"
  shift
else
  ACTION="status"
fi

TUNNEL_NAME="ngram-inference"
CLOUDFLARED_CONFIG=""
TUNNEL_URL=""
GATEWAY_HOST="127.0.0.1"
GATEWAY_PORT="8010"
LEAVE_OLLAMA_RUNNING=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -TunnelName)
      TUNNEL_NAME="$2"
      shift 2
      ;;
    -CloudflaredConfig)
      CLOUDFLARED_CONFIG="$2"
      shift 2
      ;;
    -TunnelUrl)
      TUNNEL_URL="$2"
      shift 2
      ;;
    -GatewayHost)
      GATEWAY_HOST="$2"
      shift 2
      ;;
    -GatewayPort)
      GATEWAY_PORT="$2"
      shift 2
      ;;
    -LeaveOllamaRunning)
      LEAVE_OLLAMA_RUNNING=1
      shift
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "${CLOUDFLARED_CONFIG}" ]]; then
  CLOUDFLARED_CONFIG="${HOME}/.cloudflared/config.yml"
fi

write_step() {
  echo "==> $1"
}

read_dotenv_value() {
  local key="$1"
  local dotenv_path="${REPO_ROOT}/.env"
  [[ -f "${dotenv_path}" ]] || return 0
  while IFS= read -r raw || [[ -n "${raw}" ]]; do
    local line
    line="$(echo "${raw}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    [[ -z "${line}" || "${line}" =~ ^# ]] && continue
    [[ "${line}" == *"="* ]] || continue
    local k v
    k="${line%%=*}"
    v="${line#*=}"
    k="$(echo "${k}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    v="$(echo "${v}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | sed "s/^['\"]//;s/['\"]$//")"
    if [[ "${k}" == "${key}" ]]; then
      echo "${v}"
      return 0
    fi
  done <"${dotenv_path}"
}

get_setting_value() {
  local key="$1"
  local value="${!key:-}"
  value="$(echo -n "${value}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  if [[ -n "${value}" ]]; then
    echo -n "${value}"
    return 0
  fi
  read_dotenv_value "${key}"
}

get_gateway_token() {
  local v
  v="${INFERENCE_GATEWAY_TOKEN:-}"
  v="$(echo -n "${v}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  if [[ -n "${v}" ]]; then
    echo -n "${v}"
    return 0
  fi
  v="${NGRAM_INFERENCE_GATEWAY_TOKEN:-}"
  v="$(echo -n "${v}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  if [[ -n "${v}" ]]; then
    echo -n "${v}"
    return 0
  fi
  v="$(read_dotenv_value INFERENCE_GATEWAY_TOKEN)"
  if [[ -n "${v}" ]]; then
    echo -n "${v}"
    return 0
  fi
  v="$(read_dotenv_value NGRAM_INFERENCE_GATEWAY_TOKEN)"
  echo -n "${v}"
}

tunnel_url_from_config() {
  local config_path="$1"
  [[ -f "${config_path}" ]] || return 0
  awk '
    /^[[:space:]]*-?[[:space:]]*hostname:[[:space:]]*/ {
      line = $0
      sub(/^[[:space:]]*-?[[:space:]]*hostname:[[:space:]]*/, "", line)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", line)
      if (line != "") {
        print "https://" line
        exit
      }
    }
  ' "${config_path}"
}

# Args: url, accepted_http_codes, optional Bearer token, optional Access client ID/secret
curl_http_code() {
  local url="$1"
  local accept="${2:-200}"
  local token="${3:-}"
  local access_id="${4:-}"
  local access_secret="${5:-}"
  local headers=()
  [[ -n "${token}" ]] && headers+=( -H "Authorization: Bearer ${token}" )
  [[ -n "${access_id}" ]] && headers+=( -H "CF-Access-Client-Id: ${access_id}" )
  [[ -n "${access_secret}" ]] && headers+=( -H "CF-Access-Client-Secret: ${access_secret}" )
  local code
  code="$(curl -sS -o /dev/null -w "%{http_code}" --max-time 3 \
    "${headers[@]}" "${url}" 2>/dev/null || echo "000")"
  local a
  for a in ${accept}; do
    [[ "${code}" == "$a" ]] && return 0
  done
  return 1
}

wait_url() {
  local url="$1"
  local retries="${2:-30}"
  local sleep_sec="${3:-1}"
  local accept="${4:-200}"
  local token="${5:-}"
  local access_id="${6:-}"
  local access_secret="${7:-}"
  local i
  for ((i = 0; i < retries; i++)); do
    if curl_http_code "${url}" "${accept}" "${token}" "${access_id}" "${access_secret}"; then
      return 0
    fi
    sleep "${sleep_sec}"
  done
  return 1
}

find_cloudflared() {
  if command -v cloudflared >/dev/null 2>&1; then
    command -v cloudflared
    return 0
  fi
  local c
  for c in /opt/homebrew/bin/cloudflared /usr/local/bin/cloudflared; do
    if [[ -x "${c}" ]]; then
      echo "${c}"
      return 0
    fi
  done
  echo "cloudflared executable not found. Install: brew install cloudflare/cloudflare/cloudflared (macOS) or use your distro package." >&2
  return 1
}

pick_python() {
  if [[ -n "${NGRAM_PYTHON:-}" ]]; then
    echo "${NGRAM_PYTHON}"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    command -v python
    return 0
  fi
  echo ""
}

list_cloudflared_pids() {
  local t="$1"
  local pid cmd
  for pid in $(pgrep -f cloudflared 2>/dev/null || true); do
    cmd="$(ps -p "${pid}" -o command= 2>/dev/null || ps -p "${pid}" -o args= 2>/dev/null || echo "")"
    [[ "${cmd}" == *cloudflared* ]] || continue
    if [[ "${cmd}" == *"tunnel run"* ]] || [[ "${cmd}" == *"${t}"* ]]; then
      echo "${pid}"
    fi
  done
}

list_gateway_pids() {
  pgrep -f "ngram\\.inference_gateway" 2>/dev/null || true
}

list_ollama_serve_pids() {
  pgrep -f "ollama.*serve" 2>/dev/null || true
}

stop_proc_set() {
  local label="$1"
  shift
  local pids=("$@")
  if [[ ${#pids[@]} -eq 0 ]]; then
    echo "${label}: not running"
    return 0
  fi
  local pid
  for pid in "${pids[@]}"; do
    if kill -9 "${pid}" 2>/dev/null; then
      echo "${label}: stopped PID ${pid}"
    else
      echo "warning: ${label}: failed to stop PID ${pid}" >&2
    fi
  done
}

LOCAL_GATEWAY_URL="http://${GATEWAY_HOST}:${GATEWAY_PORT}/health"
EFFECTIVE_TUNNEL_URL="${TUNNEL_URL}"
if [[ -z "${EFFECTIVE_TUNNEL_URL}" ]]; then
  EFFECTIVE_TUNNEL_URL="$(tunnel_url_from_config "${CLOUDFLARED_CONFIG}")"
fi
TOKEN="$(get_gateway_token)"
CF_ACCESS_TEAM_DOMAIN_VALUE="$(get_setting_value CF_ACCESS_TEAM_DOMAIN)"
CF_ACCESS_AUD_VALUE="$(get_setting_value CF_ACCESS_AUD)"
CF_ACCESS_CLIENT_ID_VALUE="$(get_setting_value NGRAM_CF_ACCESS_CLIENT_ID)"
[[ -n "${CF_ACCESS_CLIENT_ID_VALUE}" ]] || CF_ACCESS_CLIENT_ID_VALUE="$(get_setting_value CF_ACCESS_CLIENT_ID)"
CF_ACCESS_CLIENT_SECRET_VALUE="$(get_setting_value NGRAM_CF_ACCESS_CLIENT_SECRET)"
[[ -n "${CF_ACCESS_CLIENT_SECRET_VALUE}" ]] || CF_ACCESS_CLIENT_SECRET_VALUE="$(get_setting_value CF_ACCESS_CLIENT_SECRET)"

case "${ACTION}" in
  on)
    if [[ -z "${CF_ACCESS_TEAM_DOMAIN_VALUE}" || -z "${CF_ACCESS_AUD_VALUE}" \
      || -z "${CF_ACCESS_CLIENT_ID_VALUE}" || -z "${CF_ACCESS_CLIENT_SECRET_VALUE}" ]]; then
      echo "Refusing to open a public tunnel without Cloudflare Access. Set CF_ACCESS_TEAM_DOMAIN, CF_ACCESS_AUD, NGRAM_CF_ACCESS_CLIENT_ID, and NGRAM_CF_ACCESS_CLIENT_SECRET; add the same NGRAM_CF_ACCESS_* pair to every remote caller." >&2
      exit 1
    fi
    if [[ -z "${TOKEN}" ]]; then
      if curl_http_code "http://127.0.0.1:11434/api/tags" "200" "" \
        && curl_http_code "${LOCAL_GATEWAY_URL}" "401" "" \
        && [[ $(list_cloudflared_pids "${TUNNEL_NAME}" | wc -w | tr -d ' ') -gt 0 ]]; then
        echo "Gateway stack already appears to be running (token not present in current shell/.env)."
        echo "Set INFERENCE_GATEWAY_TOKEN in .env or shell to allow this command to start/restart services."
        exit 0
      fi
      echo "Missing gateway token. Set INFERENCE_GATEWAY_TOKEN (or NGRAM_INFERENCE_GATEWAY_TOKEN), or put one in .env." >&2
      exit 1
    fi

    if curl_http_code "http://127.0.0.1:11434/api/tags" "200" ""; then
      echo "ollama: already healthy"
    else
      write_step "starting ollama serve"
      if ! command -v ollama >/dev/null 2>&1; then
        echo "ollama not found on PATH." >&2
        exit 1
      fi
      nohup ollama serve >/dev/null 2>&1 &
      if ! wait_url "http://127.0.0.1:11434/api/tags" 40 1 "200" ""; then
        echo "Ollama did not become healthy at http://127.0.0.1:11434." >&2
        exit 1
      fi
      echo "ollama: healthy"
    fi

    if curl_http_code "${LOCAL_GATEWAY_URL}" "200" "${TOKEN}"; then
      echo "gateway: already healthy"
    else
      write_step "starting inference gateway"
      PY="$(pick_python)"
      if [[ -z "${PY}" ]]; then
        echo "python3/python not found; set NGRAM_PYTHON or install Python." >&2
        exit 1
      fi
      nohup env \
        INFERENCE_GATEWAY_TOKEN="${TOKEN}" \
        INFERENCE_GATEWAY_REQUIRE_CF_ACCESS=1 \
        CF_ACCESS_TEAM_DOMAIN="${CF_ACCESS_TEAM_DOMAIN_VALUE}" \
        CF_ACCESS_AUD="${CF_ACCESS_AUD_VALUE}" \
        "${PY}" -m ngram.inference_gateway >/dev/null 2>&1 &
      if ! wait_url "${LOCAL_GATEWAY_URL}" 50 1 "200" "${TOKEN}"; then
        echo "Inference gateway did not become healthy at ${LOCAL_GATEWAY_URL}." >&2
        exit 1
      fi
      echo "gateway: healthy"
    fi

    if [[ ! -f "${CLOUDFLARED_CONFIG}" ]]; then
      echo "Missing cloudflared config at '${CLOUDFLARED_CONFIG}'." >&2
      exit 1
    fi

    TUNNEL_HEALTHY=0
    if [[ -n "${EFFECTIVE_TUNNEL_URL}" ]]; then
      if curl_http_code "${EFFECTIVE_TUNNEL_URL}/health" "200" "${TOKEN}" "${CF_ACCESS_CLIENT_ID_VALUE}" "${CF_ACCESS_CLIENT_SECRET_VALUE}"; then
        TUNNEL_HEALTHY=1
      fi
    fi
    if [[ "${TUNNEL_HEALTHY}" -eq 1 ]]; then
      echo "tunnel: already healthy (${EFFECTIVE_TUNNEL_URL})"
    else
      write_step "starting cloudflared tunnel"
      CF="$(find_cloudflared)" || exit 1
      nohup "${CF}" --config "${CLOUDFLARED_CONFIG}" tunnel run "${TUNNEL_NAME}" >/dev/null 2>&1 &
      if [[ -n "${EFFECTIVE_TUNNEL_URL}" ]]; then
        if wait_url "${EFFECTIVE_TUNNEL_URL}/health" 60 1 "200" "${TOKEN}" "${CF_ACCESS_CLIENT_ID_VALUE}" "${CF_ACCESS_CLIENT_SECRET_VALUE}"; then
          echo "tunnel: healthy (${EFFECTIVE_TUNNEL_URL})"
        else
          echo "warning: Tunnel started but health probe to '${EFFECTIVE_TUNNEL_URL}/health' did not succeed yet." >&2
        fi
      else
        echo "tunnel: started (no tunnel URL discovered; pass -TunnelUrl to probe externally)"
      fi
    fi
    echo ""
    echo "Gateway stack is ON."
    ;;

  off)
    _cf=()
    while IFS= read -r line; do
      [[ -n "${line}" ]] && _cf+=("${line}")
    done < <(list_cloudflared_pids "${TUNNEL_NAME}")
    stop_proc_set "cloudflared" "${_cf[@]}"
    _gw=()
    while IFS= read -r line; do
      [[ -n "${line}" ]] && _gw+=("${line}")
    done < <(list_gateway_pids)
    stop_proc_set "inference gateway" "${_gw[@]}"
    if [[ "${LEAVE_OLLAMA_RUNNING}" -eq 1 ]]; then
      echo "ollama: left running by request"
    else
      _ol=()
      while IFS= read -r line; do
        [[ -n "${line}" ]] && _ol+=("${line}")
      done < <(list_ollama_serve_pids)
      stop_proc_set "ollama" "${_ol[@]}"
    fi
    echo ""
    echo "Gateway stack is OFF."
    ;;

  status)
    OLLAMA_HEALTHY=0
    curl_http_code "http://127.0.0.1:11434/api/tags" "200" "" && OLLAMA_HEALTHY=1 || true

    GATEWAY_HEALTHY=0
    if [[ -n "${TOKEN}" ]]; then
      curl_http_code "${LOCAL_GATEWAY_URL}" "200" "${TOKEN}" && GATEWAY_HEALTHY=1 || true
    else
      curl_http_code "${LOCAL_GATEWAY_URL}" "401" "" && GATEWAY_HEALTHY=1 || true
    fi

    TUNNEL_HEALTHY=0
    if [[ -n "${EFFECTIVE_TUNNEL_URL}" ]]; then
      if [[ -n "${TOKEN}" ]]; then
        curl_http_code "${EFFECTIVE_TUNNEL_URL}/health" "200" "${TOKEN}" "${CF_ACCESS_CLIENT_ID_VALUE}" "${CF_ACCESS_CLIENT_SECRET_VALUE}" && TUNNEL_HEALTHY=1 || true
      else
        curl_http_code "${EFFECTIVE_TUNNEL_URL}/health" "401" "" "${CF_ACCESS_CLIENT_ID_VALUE}" "${CF_ACCESS_CLIENT_SECRET_VALUE}" && TUNNEL_HEALTHY=1 || true
      fi
    fi

    _cf=()
    while IFS= read -r line; do
      [[ -n "${line}" ]] && _cf+=("${line}")
    done < <(list_cloudflared_pids "${TUNNEL_NAME}")
    _gw=()
    while IFS= read -r line; do
      [[ -n "${line}" ]] && _gw+=("${line}")
    done < <(list_gateway_pids)
    _ol=()
    while IFS= read -r line; do
      [[ -n "${line}" ]] && _ol+=("${line}")
    done < <(list_ollama_serve_pids)

    echo "Gateway status"
    echo "--------------"
    echo "ollama process(es): ${#_ol[@]}"
    echo "ollama healthy:      ${OLLAMA_HEALTHY}"
    echo "gateway process(es): ${#_gw[@]}"
    echo "gateway healthy:     ${GATEWAY_HEALTHY}"
    echo "cloudflared procs:   ${#_cf[@]}"
    if [[ -n "${EFFECTIVE_TUNNEL_URL}" ]]; then
      echo "tunnel url:          ${EFFECTIVE_TUNNEL_URL}"
      echo "tunnel healthy:      ${TUNNEL_HEALTHY}"
    else
      echo "tunnel url:          (not set)"
      echo "tunnel healthy:      (unknown)"
    fi
    if [[ -z "${TOKEN}" ]]; then
      echo "token source:        missing (status probes use 401 reachability)"
    else
      echo "token source:        present"
    fi
    if [[ -n "${CF_ACCESS_TEAM_DOMAIN_VALUE}" && -n "${CF_ACCESS_AUD_VALUE}" \
      && -n "${CF_ACCESS_CLIENT_ID_VALUE}" && -n "${CF_ACCESS_CLIENT_SECRET_VALUE}" ]]; then
      echo "cloudflare access:   configured"
    else
      echo "cloudflare access:   missing (tunnel start will fail closed)"
    fi
    ;;

  restart)
    write_step "gateway restart: stopping stack"
    OFF_ARGS=( -TunnelName "${TUNNEL_NAME}" -CloudflaredConfig "${CLOUDFLARED_CONFIG}" -GatewayHost "${GATEWAY_HOST}" -GatewayPort "${GATEWAY_PORT}" )
    [[ -n "${TUNNEL_URL}" ]] && OFF_ARGS+=( -TunnelUrl "${TUNNEL_URL}" )
    [[ "${LEAVE_OLLAMA_RUNNING}" -eq 1 ]] && OFF_ARGS+=( -LeaveOllamaRunning )
    bash "${SCRIPT_PATH}" off "${OFF_ARGS[@]}"
    sleep 2
    write_step "gateway restart: starting stack"
    ON_ARGS=(
      -TunnelName "${TUNNEL_NAME}"
      -GatewayHost "${GATEWAY_HOST}"
      -GatewayPort "${GATEWAY_PORT}"
    )
    [[ -n "${CLOUDFLARED_CONFIG}" ]] && ON_ARGS+=( -CloudflaredConfig "${CLOUDFLARED_CONFIG}" )
    [[ -n "${TUNNEL_URL}" ]] && ON_ARGS+=( -TunnelUrl "${TUNNEL_URL}" )
    bash "${SCRIPT_PATH}" on "${ON_ARGS[@]}"
    echo ""
    echo "Gateway stack RESTART complete."
    ;;

  *)
    echo "Unknown action: ${ACTION} (use on, off, status, restart)" >&2
    exit 2
    ;;
esac
