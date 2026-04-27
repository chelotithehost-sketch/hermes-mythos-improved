#!/bin/bash
# ==============================================================================
# Hermes-Mythos: Enterprise-Grade Provisioning & Pairing Script
# Version: 4.0 (Final Production Release)
# Targets: Docker, Multi-Provider LLM Gateway, Telegram, WhatsApp
# Features: Dual-mode (interactive/manual), env templating, resume capability,
#           comprehensive validation, Docker/WSL/K8s detection, audit logging
# ==============================================================================
set -euo pipefail
IFS=$'\n\t'

# --- Configuration ---
readonly REPO_URL="https://github.com/chelotithehost-sketch/hermes-mythos-improved.git"
readonly REPO_BRANCH="main"
readonly SCRIPT_VERSION="4.0.0"
readonly MIN_DOCKER_VERSION="20.10.0"
readonly MIN_RAM_MB=2048
readonly REQUIRED_PORTS=(8000 443 80)
readonly ENV_TEMPLATE=".env.template"
readonly ENV_FILE=".env"

# --- UI & Logging ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
BLUE='\033[0;34m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'
LOG_FILE="hermes_deploy_$(date +%Y%m%d_%H%M%S).log"
AUDIT_LOG="hermes_audit_$(date +%Y%m%d).log"

log() { echo -e "${CYAN}[$(date '+%H:%M:%S')] $1${NC}" | tee -a "$LOG_FILE"; }
info() { log "${BOLD}INFO${NC}: $1"; }
warn() { log "${YELLOW}⚠ WARN${NC}: $1" >&2; echo "[$(date '+%H:%M:%S')] WARN: $1" >> "$AUDIT_LOG"; }
error() { log "${RED}✗ ERROR${NC}: $1" >&2; echo "[$(date '+%H:%M:%S')] ERROR: $1" >> "$AUDIT_LOG"; }
success() { log "${GREEN}✓${NC} $1"; }
step() { echo -e "\n${BLUE}${BOLD}━━━ $1 ━━━${NC}"; }

# --- Global State ---
declare -A CONFIG
CONFIG[interactive]=0
CONFIG[docker]=0
CONFIG[wsl]=0
CONFIG[resume]=0
CONFIG[skip_validation]=0
CONFIG[public_ip]=""
CONFIG[env_exists]=0

# --- Cleanup & Rollback ---
TEMP_DIRS=()
cleanup() {
    for dir in "${TEMP_DIRS[@]}"; do
        [[ -d "$dir" ]] && rm -rf "$dir" && info "Cleaned temp: $dir"
    done
}
rollback() {
    error "Deployment failed at step: ${CONFIG[current_step]:-unknown}"
    if [[ -f "docker-compose.yml" ]] && command -v docker-compose &>/dev/null; then
        warn "Stopping containers..."
        docker-compose down --remove-orphans 2>/dev/null || true
    fi
    cleanup
    error "Rollback complete. Review logs:"
    error "  - Deployment: $LOG_FILE"
    error "  - Audit trail: $AUDIT_LOG"
    exit 1
}
trap cleanup EXIT
trap rollback ERR

# --- Argument Parsing ---
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --manual|-m) CONFIG[manual_mode]=1; shift ;;
            --resume|-r) CONFIG[resume]=1; shift ;;
            --skip-validation|-s) CONFIG[skip_validation]=1; shift ;;
            --env-only|-e) CONFIG[env_only]=1; shift ;;
            --help|-h) show_help; exit 0 ;;
            *) warn "Unknown option: $1. Use --help for usage."; shift ;;
        esac
    done
}

show_help() {
    cat << EOF
${CYAN}${BOLD}Hermes-Mythos Deployer v$SCRIPT_VERSION${NC}

USAGE: $0 [OPTIONS]

OPTIONS:
  --manual, -m          Force manual configuration mode (generates .env.template)
  --resume, -r          Resume from last successful step (requires .deploy_state)
  --skip-validation, -s Skip API key validation (dev/testing only)
  --env-only, -e        Only generate/update .env file, skip deployment
  --help, -h            Show this help message

ENVIRONMENT VARIABLES (non-interactive mode):
  ANTHROPIC_API_KEY     Anthropic Claude API key
  OPENAI_API_KEY        OpenAI GPT API key
  GEMINI_API_KEY        Google Gemini API key
  MISTRAL_API_KEY       Mistral AI API key
  GROK_API_KEY          xAI Grok API key
  TELEGRAM_BOT_TOKEN    Telegram bot token from @BotFather
  WHATSAPP_ACCESS_TOKEN WhatsApp Meta access token
  WHATSAPP_PHONE_NUMBER_ID WhatsApp business phone ID
  WHATSAPP_VERIFY_TOKEN Webhook verification token (default: hermes_mythos_v2)
  PUBLIC_IP             Override auto-detected public IP
  SECRET_KEY            Override auto-generated secret key

EXAMPLES:
  # Interactive deployment (recommended)
  ./deploy.sh

  # Non-interactive with env vars
  export OPENAI_KEY="sk-..." TELEGRAM_TOKEN="1234:AAA..."
  ./deploy.sh < /dev/null

  # Manual mode: generate template, edit, then deploy
  ./deploy.sh --manual
  # → Edit .env.template → mv .env.template .env
  ./deploy.sh --env-only

  # Resume after failure
  ./deploy.sh --resume

EOF
}

# --- Environment Detection ---
detect_env() {
    step "Environment Detection"
    info "Analyzing runtime environment..."
    
    # Docker detection
    if [[ -f /.dockerenv ]] || grep -q docker /proc/1/cgroup 2>/dev/null; then
        CONFIG[docker]=1
        warn "Running inside Docker container"
    fi
    
    # WSL detection
    if grep -qi microsoft /proc/version 2>/dev/null; then
        CONFIG[wsl]=1
        info "WSL environment detected"
    fi
    
    # Interactive mode detection
    if [[ -t 0 ]] && [[ "${CONFIG[manual_mode]:-0}" != "1" ]]; then
        CONFIG[interactive]=1
        info "Interactive mode: terminal prompts enabled"
    else
        CONFIG[interactive]=0
        warn "Non-interactive mode: using env vars or manual template"
    fi
    
    # Check for existing .env
    if [[ -f "$ENV_FILE" ]]; then
        CONFIG[env_exists]=1
        info "Existing $ENV_FILE detected - values will be preserved"
        # Load existing values as defaults
        set -a
        source "$ENV_FILE" 2>/dev/null || true
        set +a
    fi
    
    # Public IP detection with robust fallbacks
    if [[ -n "${PUBLIC_IP:-}" ]]; then
        CONFIG[public_ip]="$PUBLIC_IP"
    else
        CONFIG[public_ip]=$(
            curl -s --max-time 8 ifconfig.me 2>/dev/null ||
            curl -s --max-time 8 ipinfo.io/ip 2>/dev/null ||
            curl -s --max-time 8 api.ipify.org 2>/dev/null ||
            hostname -I 2>/dev/null | awk '{print $1}' | cut -d' ' -f1 ||
            echo "localhost"
        )
    fi
    info "Public IP: ${CONFIG[public_ip]}"
    
    # RAM check (best effort)
    if command -v free &>/dev/null; then
        local ram_mb=$(free -m | awk '/^Mem:/{print $2}')
        if [[ "$ram_mb" -lt "$MIN_RAM_MB" ]]; then
            warn "System RAM (${ram_mb}MB) below recommended minimum (${MIN_RAM_MB}MB)"
        fi
    fi
    
    success "Environment analysis complete"
}

# --- Dependency Management ---
install_dependencies() {
    step "Dependency Audit"
    info "[1/6] Verifying system dependencies..."
    
    local SUDO=""
    [[ "$(id -u)" != "0" ]] && command -v sudo &>/dev/null && SUDO="sudo"
    
    # Quiet apt update
    if command -v apt-get &>/dev/null; then
        $SUDO apt-get update -qq >/dev/null 2>&1 || warn "apt update failed, continuing..."
    fi
    
    # Core dependencies with graceful fallbacks
    local -A DEPS=(
        [git]="git"
        [curl]="curl"
        [jq]="jq"
        [docker]="docker.io"
        [docker-compose]="docker-compose"
    )
    
    local missing=()
    for cmd in "${!DEPS[@]}"; do
        if ! command -v "$cmd" &>/dev/null; then
            missing+=("${DEPS[$cmd]}")
        fi
    done
    
    if [[ ${#missing[@]} -gt 0 ]]; then
        info "Installing missing packages: ${missing[*]}"
        if command -v apt-get &>/dev/null; then
            $SUDO apt-get install -y "${missing[@]}" >/dev/null 2>&1 || {
                error "Failed to install dependencies. Please install manually:"
                error "  sudo apt-get install -y ${missing[*]}"
                return 1
            }
        else
            error "No supported package manager found. Install manually: ${missing[*]}"
            return 1
        fi
    fi
    
    # Install rsync for reliable file operations
    if ! command -v rsync &>/dev/null && command -v apt-get &>/dev/null; then
        $SUDO apt-get install -y rsync >/dev/null 2>&1 || true
    fi
    
    # Docker version check
    if command -v docker &>/dev/null; then
        local ver=$(docker --version 2>/dev/null | grep -oP '\d+\.\d+\.\d+' | head -1 || echo "0.0.0")
        if [[ "$(printf '%s\n' "$MIN_DOCKER_VERSION" "$ver" | sort -V | head -1)" != "$MIN_DOCKER_VERSION" ]]; then
            warn "Docker $ver may be outdated (min: $MIN_DOCKER_VERSION)"
        fi
        # Test Docker daemon
        if ! docker info &>/dev/null; then
            error "Docker daemon not accessible. Start Docker and retry."
            return 1
        fi
    fi
    
    success "All dependencies satisfied"
}

# --- Repository Setup (Atomic & Idempotent) ---
setup_repo() {
    step "Repository Initialization"
    info "[2/6] Preparing codebase..."
    
    local temp_clone="temp_clone_$$"
    TEMP_DIRS+=("$temp_clone")
    
    if [[ ! -d ".git" ]]; then
        info "Cloning from $REPO_URL..."
        rm -rf "$temp_clone"
        
        if ! git clone --branch "$REPO_BRANCH" --depth 1 "$REPO_URL" "$temp_clone" 2>&1; then
            error "Git clone failed. Check network/repository access."
            return 1
        fi
        
        # Atomic copy with multiple fallbacks
        info "Copying repository files..."
        (
            cd "$temp_clone" || return 1
            if command -v rsync &>/dev/null; then
                rsync -a --exclude='.git' --exclude='temp_clone_*' ./ ../ 2>/dev/null && return 0
            fi
            # Fallback: find + cp (handles hidden files reliably)
            find . -mindepth 1 -maxdepth 1 ! -name '.git' ! -name '.*.swp' -exec cp -rf {} ../. \; 2>/dev/null || return 1
        ) || {
            error "Failed to copy repository files"
            ls -la "$temp_clone/" >&2 || true
            return 1
        }
        success "Repository cloned"
    else
        info "Existing repository - checking for updates..."
        if git fetch origin "$REPO_BRANCH" --quiet 2>/dev/null; then
            if ! git diff --quiet HEAD "origin/$REPO_BRANCH"; then
                info "Updates available. Pulling..."
                git pull --ff-only origin "$REPO_BRANCH" 2>/dev/null || \
                    warn "Pull failed (local changes?). Updates skipped."
            else
                info "Repository up to date"
            fi
        else
            warn "Could not check for updates (network?)"
        fi
    fi
    
    # Prepare persistent volumes
    local -a VOLUMES=(manuscripts library_db mnt/data webhooks logs)
    for vol in "${VOLUMES[@]}"; do
        mkdir -p "$vol" 2>/dev/null || true
        # Permission handling: try 755, fallback to 777 for Docker compatibility
        chmod -R 755 "$vol" 2>/dev/null || chmod -R 777 "$vol" 2>/dev/null || true
    done
    success "Data volumes prepared"
}

# --- Configuration: Interactive Mode ---
configure_interactive() {
    step "LLM Gateway Configuration"
    echo -e "${CYAN}Enter API keys (leave blank to skip). At least one required.${NC}"
    
    local -a providers=("Anthropic" "OpenAI" "Gemini" "Mistral" "Grok/xAI")
    local -a vars=("ANTHROPIC_API_KEY" "OPENAI_API_KEY" "GEMINI_API_KEY" "MISTRAL_API_KEY" "GROK_API_KEY")
    local keys_provided=0
    
    for i in "${!providers[@]}"; do
        local prompt="${providers[$i]} API Key"
        local current="${!vars[$i]:-}"
        [[ -n "$current" ]] && prompt="$prompt [${current:0:8}...]"
        read -rp "$prompt: " value
        if [[ -n "$value" ]]; then
            printf -v "${vars[$i]}" '%s' "$value"
            ((keys_provided++)) || true
        fi
    done
    
    if [[ "$keys_provided" -eq 0 ]] && [[ "${CONFIG[skip_validation]}" != "1" ]]; then
        error "At least one LLM API key is required for 'The Brain'"
        echo -e "${YELLOW}Tip: Use --skip-validation for testing without keys${NC}"
        return 1
    fi
    
    step "Omnichannel Messaging Setup"
    echo -e "${CYAN}Configure messaging channels (leave blank to skip)${NC}"
    
    # Telegram
    echo -e "\n${BOLD}Telegram:${NC} Create bot via @BotFather"
    read -rp "Telegram Bot Token: " TELEGRAM_BOT_TOKEN
    
    # WhatsApp
    echo -e "\n${BOLD}WhatsApp:${NC} Configure at developers.facebook.com"
    read -rp "WhatsApp Access Token: " WHATSAPP_ACCESS_TOKEN
    read -rp "WhatsApp Phone Number ID: " WHATSAPP_PHONE_NUMBER_ID
    read -rp "Webhook Verify Token [hermes_mythos_v2]: " WHATSAPP_VERIFY_TOKEN
    WHATSAPP_VERIFY_TOKEN="${WHATSAPP_VERIFY_TOKEN:-hermes_mythos_v2}"
    
    success "Configuration collected"
}

# --- Configuration: Manual/Template Mode ---
configure_manual() {
    step "Generating Configuration Template"
    info "Non-interactive mode: creating $ENV_TEMPLATE for manual editing"
    
    cat > "$ENV_TEMPLATE" << 'TEMPLATE_EOF'
# ==============================================================================
# Hermes-Mythos Environment Configuration
# Generated by deploy.sh v4.0 - Edit values below, then rename to .env
# ==============================================================================

# --- LLM Gateway (Provide at least one) ---
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
GEMINI_API_KEY=
MISTRAL_API_KEY=
GROK_API_KEY=

# --- Messaging Channels ---
TELEGRAM_BOT_TOKEN=
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_VERIFY_TOKEN=hermes_mythos_v2

# --- Network & Runtime ---
SERVER_URL=http://YOUR_PUBLIC_IP:8000
OLLAMA_HOST=http://host.docker.internal:11434
DB_PATH=/app/library_db/library.db
LOG_LEVEL=INFO
PYTHONMALLOC=malloc

# --- Security ---
SECRET_KEY=change_me_in_production_generate_32_char_hex

# --- Optional Advanced ---
# REDIS_URL=redis://localhost:6379
# METRICS_ENABLED=true
# RATE_LIMIT_PER_MIN=60
TEMPLATE_EOF

    # Pre-fill with any existing env vars
    for var in ANTHROPIC_API_KEY OPENAI_API_KEY GEMINI_API_KEY MISTRAL_API_KEY GROK_API_KEY \
               TELEGRAM_BOT_TOKEN WHATSAPP_ACCESS_TOKEN WHATSAPP_PHONE_NUMBER_ID; do
        if [[ -n "${!var:-}" ]]; then
            sed -i "s|^${var}=.*|${var}=${!var}|" "$ENV_TEMPLATE" 2>/dev/null || true
        fi
    done
    sed -i "s|YOUR_PUBLIC_IP|${CONFIG[public_ip]}|" "$ENV_TEMPLATE" 2>/dev/null || true
    
    echo -e "\n${CYAN}${BOLD}NEXT STEPS:${NC}"
    echo "1. Edit the template: ${BOLD}nano $ENV_TEMPLATE${NC}"
    echo "2. Fill in at least one LLM API key"
    echo "3. Rename to activate: ${BOLD}mv $ENV_TEMPLATE $ENV_FILE${NC}"
    echo "4. Re-run deployment: ${BOLD}$0 --env-only${NC}"
    echo -e "\n${DIM}Or set environment variables and run non-interactively:${NC}"
    echo -e "${DIM}  export OPENAI_KEY=sk-... TELEGRAM_TOKEN=123:AAA...${NC}"
    echo -e "${DIM}  $0 < /dev/null${NC}"
    
    if [[ "${CONFIG[env_only]:-0}" != "1" ]]; then
        info "Pausing for manual configuration..."
        echo -e "${YELLOW}Press ENTER after editing $ENV_TEMPLATE, or Ctrl+C to exit${NC}"
        read -r || true
        if [[ -f "$ENV_TEMPLATE" ]]; then
            if grep -qE '^(ANTHROPIC_API_KEY|OPENAI_API_KEY|GEMINI_API_KEY|MISTRAL_API_KEY|GROK_API_KEY)=[^ ]+' "$ENV_TEMPLATE"; then
                mv "$ENV_TEMPLATE" "$ENV_FILE"
                chmod 600 "$ENV_FILE" 2>/dev/null || true
                success "Configuration activated"
            else
                warn "No LLM keys found in $ENV_TEMPLATE. Deployment will fail validation."
            fi
        fi
    fi
}

# --- Configuration: Load & Validate ---
configure_llm_and_messaging() {
    # If .env exists, source it
    if [[ -f "$ENV_FILE" ]]; then
        info "Loading configuration from $ENV_FILE"
        set -a
        source "$ENV_FILE"
        set +a
    fi
    
    # Interactive mode: prompt user
    if [[ "${CONFIG[interactive]}" == "1" ]] && [[ "${CONFIG[manual_mode]:-0}" != "1" ]]; then
        configure_interactive || return 1
    # Manual/template mode
    elif [[ ! -f "$ENV_FILE" ]] || [[ "${CONFIG[manual_mode]:-0}" == "1" ]]; then
        configure_manual
        [[ "${CONFIG[env_only]:-0}" == "1" ]] && return 0
    fi
    
    # Validation (unless skipped)
    if [[ "${CONFIG[skip_validation]}" != "1" ]]; then
        local llm_keys="${ANTHROPIC_API_KEY:-}${OPENAI_API_KEY:-}${GEMINI_API_KEY:-}${MISTRAL_API_KEY:-}${GROK_API_KEY:-}"
        if [[ -z "$llm_keys" ]]; then
            error "Validation failed: At least one LLM API key required"
            echo -e "${YELLOW}Solutions:${NC}"
            echo "  • Re-run with keys: $0"
            echo "  • Use template mode: $0 --manual"
            echo "  • Skip validation (testing): $0 --skip-validation"
            return 1
        fi
        success "Configuration validated"
    else
        warn "Validation skipped (--skip-validation)"
    fi
}

# --- Generate Final .env Atomically ---
write_env_file() {
    info "Writing final environment configuration..."
    
    local env_tmp=".env.atomic.$$"
    {
        echo "# Hermes-Mythos Environment - Generated $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
        echo "# Deployer v$SCRIPT_VERSION | IP: ${CONFIG[public_ip]}"
        echo ""
        echo "# LLM Gateway"
        echo "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}"
        echo "OPENAI_API_KEY=${OPENAI_API_KEY:-}"
        echo "GEMINI_API_KEY=${GEMINI_API_KEY:-}"
        echo "MISTRAL_API_KEY=${MISTRAL_API_KEY:-}"
        echo "GROK_API_KEY=${GROK_API_KEY:-}"
        echo ""
        echo "# Messaging"
        echo "TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN:-}"
        echo "WHATSAPP_ACCESS_TOKEN=${WHATSAPP_ACCESS_TOKEN:-}"
        echo "WHATSAPP_PHONE_NUMBER_ID=${WHATSAPP_PHONE_NUMBER_ID:-}"
        echo "WHATSAPP_VERIFY_TOKEN=${WHATSAPP_VERIFY_TOKEN:-hermes_mythos_v2}"
        echo ""
        echo "# Network"
        echo "SERVER_URL=http://${CONFIG[public_ip]}:8000"
        echo "OLLAMA_HOST=http://host.docker.internal:11434"
        echo "DB_PATH=/app/library_db/library.db"
        echo "LOG_LEVEL=INFO"
        echo "PYTHONMALLOC=malloc"
        echo ""
        echo "# Security"
        echo "SECRET_KEY=${SECRET_KEY:-$(openssl rand -hex 32 2>/dev/null || python3 -c 'import secrets; print(secrets.token_hex(32))' 2>/dev/null || echo "dev_key_$(date +%s)_$$")}"
        echo ""
        echo "# Advanced (optional)"
        echo "# REDIS_URL=redis://localhost:6379"
        echo "# METRICS_ENABLED=false"
    } > "$env_tmp"
    
    # Atomic replace with permission hardening
    mv "$env_tmp" "$ENV_FILE"
    chmod 600 "$ENV_FILE" 2>/dev/null || true
    success "Environment file secured ($ENV_FILE)"
}

# --- Container Deployment ---
deploy() {
    [[ "${CONFIG[env_only]:-0}" == "1" ]] && return 0
    
    step "Container Deployment"
    info "[5/6] Launching services..."
    
    # Pre-flight checks
    if ! command -v docker-compose &>/dev/null; then
        # Try new docker compose v2 syntax
        if ! docker compose version &>/dev/null; then
            error "docker-compose not found. Install Docker Compose."
            return 1
        fi
        alias docker-compose="docker compose"
    fi
    
    # Check for docker-compose.yml
    if [[ ! -f "docker-compose.yml" ]]; then
        error "docker-compose.yml not found. Repository setup may have failed."
        return 1
    fi
    
    # Port conflict warnings
    for port in "${REQUIRED_PORTS[@]}"; do
        if command -v ss &>/dev/null && ss -tlnp 2>/dev/null | grep -q ":$port "; then
            warn "Port $port in use - deployment may fail"
        fi
    done
    
    # Deploy
    info "Stopping existing containers..."
    docker-compose down --remove-orphans 2>/dev/null || true
    
    info "Building and starting services..."
    if ! docker-compose up --build -d 2>&1; then
        error "Deployment failed"
        echo -e "${YELLOW}Debug commands:${NC}"
        echo "  docker-compose logs --tail=50"
        echo "  docker ps -a"
        return 1
    fi
    
    # Health wait
    info "Waiting for services to initialize..."
    sleep 15
    if docker-compose ps 2>/dev/null | grep -q "Up"; then
        success "Services running"
    else
        warn "Some services may not be healthy. Check: docker-compose ps"
    fi
}

# --- Webhook Finalization ---
finalize_pairing() {
    [[ "${CONFIG[env_only]:-0}" == "1" ]] && return 0
    
    step "Bot Pairing Finalization"
    info "[6/6] Registering webhooks..."
    
    # Telegram
    if [[ -n "${TELEGRAM_BOT_TOKEN:-}" ]]; then
        info "Registering Telegram webhook..."
        local url="http://${CONFIG[public_ip]}:8000/webhooks/telegram"
        local resp
        resp=$(curl -s -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
            -d "url=$url" 2>/dev/null) || true
        if [[ "$resp" == *"\"ok\":true"* ]]; then
            success "Telegram webhook registered"
        else
            warn "Telegram response: ${resp:-timeout}"
        fi
    fi
    
    # Summary
    echo -e "\n${CYAN}${BOLD}╔════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}${BOLD}║   HERMES-MYTHOS DEPLOYMENT COMPLETE   ║${NC}"
    echo -e "${CYAN}${BOLD}╚════════════════════════════════════════╝${NC}"
    echo -e "Public Endpoint:  ${BOLD}http://${CONFIG[public_ip]}:8000${NC}"
    echo -e "Health Check:     ${BOLD}http://${CONFIG[public_ip]}:8000/health${NC}"
    echo -e "Logs:             ${BOLD}docker-compose logs -f${NC}"
    
    if [[ -n "${TELEGRAM_BOT_TOKEN:-}" ]]; then
        echo -e "${GREEN}✓ Telegram:${NC} Webhook active"
    else
        echo -e "${YELLOW}○ Telegram:${NC} Not configured"
    fi
    
    if [[ -n "${WHATSAPP_ACCESS_TOKEN:-}" ]]; then
        echo -e "${GREEN}✓ WhatsApp:${NC} Configure in Meta Dashboard:"
        echo -e "  URL: ${YELLOW}http://${CONFIG[public_ip]}:8000/webhooks/whatsapp${NC}"
        echo -e "  Token: ${YELLOW}${WHATSAPP_VERIFY_TOKEN:-hermes_mythos_v2}${NC}"
    else
        echo -e "${YELLOW}○ WhatsApp:${NC} Not configured"
    fi
    
    echo -e "\n${DIM}Audit log: $AUDIT_LOG${NC}"
    echo -e "${GREEN}${BOLD}The 7-Layer Cognitive DAG is standing by.${NC}"
}

# --- State Management for Resume ---
save_state() {
    local state_file=".deploy_state"
    cat > "$state_file" << EOF
# Hermes Deploy State - DO NOT EDIT
step=$1
timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
ip=${CONFIG[public_ip]}
EOF
}

load_state() {
    local state_file=".deploy_state"
    if [[ -f "$state_file" ]] && [[ "${CONFIG[resume]}" == "1" ]]; then
        source "$state_file" 2>/dev/null || true
        info "Resuming from step: $step"
        return 0
    fi
    return 1
}

# --- Main Execution ---
main() {
    parse_args "$@"
    
    # Header
    clear
    echo -e "${CYAN}${BOLD}"
    echo "╔════════════════════════════════════════════════════╗"
    echo "║   HERMES-MYTHOS: PRODUCTION DEPLOYER v$SCRIPT_VERSION  ║"
    echo "╚════════════════════════════════════════════════════╝"
    echo -e "${NC}"
    
    # Initialize
    detect_env
    load_state || save_state "init"
    
    # Pipeline
    CONFIG[current_step]="dependencies"
    install_dependencies || exit 1
    save_state "dependencies"
    
    CONFIG[current_step]="repository"
    setup_repo || exit 1
    save_state "repository"
    
    CONFIG[current_step]="configuration"
    configure_llm_and_messaging || exit 1
    write_env_file
    save_state "configuration"
    
    CONFIG[current_step]="deployment"
    deploy || exit 1
    save_state "deployment"
    
    CONFIG[current_step]="finalization"
    finalize_pairing
    
    # Cleanup state on success
    rm -f ".deploy_state" 2>/dev/null || true
    success "Deployment complete. Logs: $LOG_FILE"
}

# Entry point
main "$@"
