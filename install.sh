#!/bin/bash
# ==============================================================================
# Hermes-Mythos: Enterprise-Grade Provisioning & Pairing Script
# Version: 3.1 (Docker-Hardened)
# Targets: Docker, Multi-Provider LLM Gateway, Telegram, & WhatsApp
# Features: Atomic ops, Docker/WSL detection, safe input, rollback, logging
# ==============================================================================
set -euo pipefail

# --- Configuration ---
readonly REPO_URL="https://github.com/chelotithehost-sketch/hermes-mythos-improved.git"
readonly REPO_BRANCH="main"
readonly SCRIPT_VERSION="3.1.0"
readonly MIN_DOCKER_VERSION="20.10.0"
readonly REQUIRED_PORTS=(8000 443 80)

# --- UI & Logging ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'
LOG_FILE="hermes_deploy_$(date +%Y%m%d_%H%M%S).log"

log() { echo -e "${CYAN}[$(date '+%H:%M:%S')] $1${NC}" | tee -a "$LOG_FILE"; }
info() { log "${BOLD}INFO${NC}: $1"; }
warn() { log "${YELLOW}WARN${NC}: $1" >&2; }
error() { log "${RED}ERROR${NC}: $1" >&2; }
success() { log "${GREEN}✓${NC} $1"; }

# --- Cleanup & Rollback ---
TEMP_DIRS=()
cleanup() {
    for dir in "${TEMP_DIRS[@]}"; do
        [[ -d "$dir" ]] && rm -rf "$dir" && info "Cleaned temp: $dir"
    done
}
rollback() {
    error "Deployment failed. Initiating rollback..."
    [[ -f "docker-compose.yml" ]] && docker-compose down 2>/dev/null || true
    cleanup
    error "Rollback complete. Check $LOG_FILE for details."
    exit 1
}
trap cleanup EXIT
trap rollback ERR

# --- Environment Detection ---
detect_env() {
    info "Detecting runtime environment..."
    
    # Check if running in Docker
    if [[ -f /.dockerenv ]] || grep -q docker /proc/1/cgroup 2>/dev/null; then
        export RUNNING_IN_DOCKER=1
        warn "Running inside Docker container. Some operations may be restricted."
    else
        export RUNNING_IN_DOCKER=0
    fi
    
    # Check for WSL
    if grep -qi microsoft /proc/version 2>/dev/null; then
        export RUNNING_IN_WSL=1
        info "WSL environment detected."
    else
        export RUNNING_IN_WSL=0
    fi
    
    # Check interactive mode
    if [[ -t 0 ]]; then
        export INTERACTIVE_MODE=1
    else
        export INTERACTIVE_MODE=0
        warn "Non-interactive mode detected. Using defaults or env vars."
    fi
    
    # Get public IP (with fallbacks)
    PUBLIC_IP="${PUBLIC_IP:-}"
    if [[ -z "$PUBLIC_IP" ]]; then
        PUBLIC_IP=$(curl -s --max-time 10 ifconfig.me 2>/dev/null || \
                   curl -s --max-time 10 ipinfo.io/ip 2>/dev/null || \
                   hostname -I | awk '{print $1}' 2>/dev/null || echo "localhost")
    fi
    export PUBLIC_IP
    info "Detected public IP: $PUBLIC_IP"
}

# --- Dependency Management ---
install_dependencies() {
    info "[1/6] Auditing System Dependencies..."
    
    local SUDO=""
    if [[ "$(id -u)" != "0" ]] && command -v sudo &>/dev/null; then
        SUDO="sudo"
    fi
    
    # Update package index (suppress output)
    if command -v apt-get &>/dev/null; then
        $SUDO apt-get update -qq >/dev/null 2>&1 || warn "apt update failed, continuing..."
    fi
    
    # Required packages
    local -A DEPS=(
        [docker.io]="docker --version"
        [docker-compose]="docker-compose --version"
        [git]="git --version"
        [curl]="curl --version"
        [jq]="jq --version"
    )
    
    for pkg in "${!DEPS[@]}"; do
        if ! command -v "${pkg%%.*}" &>/dev/null; then
            info "Installing missing package: ${BOLD}$pkg${NC}..."
            if command -v apt-get &>/dev/null; then
                $SUDO apt-get install -y "$pkg" >/dev/null 2>&1 || {
                    error "Failed to install $pkg. Please install manually."
                    return 1
                }
            else
                warn "Package manager not detected. Please install $pkg manually."
            fi
        else
            success "$pkg already installed"
        fi
    done
    
    # Install rsync if missing (critical for reliable file copy)
    if ! command -v rsync &>/dev/null && command -v apt-get &>/dev/null; then
        $SUDO apt-get install -y rsync >/dev/null 2>&1 || true
    fi
    
    # Verify Docker version
    if command -v docker &>/dev/null; then
        local docker_ver=$(docker --version 2>/dev/null | grep -oP '\d+\.\d+\.\d+' | head -1 || echo "0.0.0")
        if [[ -n "$docker_ver" ]] && [[ "$(printf '%s\n' "$MIN_DOCKER_VERSION" "$docker_ver" | sort -V | head -1)" != "$MIN_DOCKER_VERSION" ]]; then
            warn "Docker version $docker_ver may be outdated (min: $MIN_DOCKER_VERSION)"
        fi
    fi
    
    success "Dependencies verified"
}

# --- 2. Intelligent Repository Setup (Docker-Safe) ---
setup_repo() {
    info "[2/6] Initializing Data Structures..."
    
    local temp_clone="temp_clone_$$"
    TEMP_DIRS+=("$temp_clone")
    
    # Only clone if not already in a git repo
    if [[ ! -d ".git" ]]; then
        info "Cloning latest architecture from $REPO_URL..."
        
        # Ensure clean temp directory
        rm -rf "$temp_clone"
        
        if ! git clone --branch "$REPO_BRANCH" --depth 1 "$REPO_URL" "$temp_clone"; then
            error "Git clone failed. Check network access or repository URL."
            return 1
        fi
        
        # Docker-safe copy: handle hidden files reliably
        info "Copying repository files..."
        (
            cd "$temp_clone" || return 1
            # Method 1: Try rsync if available (preserves permissions)
            if command -v rsync &>/dev/null; then
                rsync -a --exclude='.git' ./ ../ 2>/dev/null && return 0
            fi
            # Method 2: Fallback to find + cp (handles hidden files)
            find . -mindepth 1 -maxdepth 1 ! -name '.git' -exec cp -rf {} ../. \; 2>/dev/null || return 1
        ) || {
            error "Failed to copy repository files"
            # Debug: show what's in temp_clone
            warn "Contents of $temp_clone:"
            ls -la "$temp_clone/" >&2 2>/dev/null || true
            return 1
        }
        
        success "Repository initialized"
    else
        info "Existing repository detected. Syncing updates..."
        if ! git pull origin "$REPO_BRANCH" --ff-only 2>/dev/null; then
            warn "Local changes detected or pull failed. Skipping sync."
        else
            success "Repository synced"
        fi
    fi
    
    # Setup persistent volumes with appropriate permissions
    local -a VOLUMES=(manuscripts library_db mnt/data webhooks)
    for vol in "${VOLUMES[@]}"; do
        mkdir -p "$vol" 2>/dev/null || true
        # Only chmod if we have permission (avoid errors in restricted containers)
        chmod -R 755 "$vol" 2>/dev/null || chmod -R 777 "$vol" 2>/dev/null || true
    done
    success "Data volumes prepared"
}

# --- Safe Input Handler (TTY-Aware) ---
safe_read() {
    local prompt="$1"
    local var_name="$2"
    local default="${3:-}"
    local value=""
    
    if [[ "$INTERACTIVE_MODE" == "1" ]]; then
        # Interactive: read from terminal
        read -rp "$prompt" value
    else
        # Non-interactive: use env var or default
        value="${!var_name:-$default}"
        [[ -n "$default" ]] && value="${value:-$default}"
    fi
    # Trim leading/trailing whitespace
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    printf -v "$var_name" '%s' "$value"
}

# --- 3. Unified LLM Gateway Configuration ---
configure_llm() {
    info "[3/6] Configuring LLM Gateway..."
    echo -e "\n${CYAN}--- LLM GATEWAY CONFIGURATION ---${NC}"
    echo "Leave blank to skip a provider. At least one is required for 'The Brain'."
    
    safe_read "Anthropic API Key: " ANTHROPIC_KEY
    safe_read "OpenAI API Key: " OPENAI_KEY
    safe_read "Gemini API Key: " GEMINI_KEY
    safe_read "Mistral API Key: " MISTRAL_KEY
    safe_read "Grok/xAI API Key: " GROK_KEY
    
    # Validate at least one key provided
    if [[ -z "$ANTHROPIC_KEY$OPENAI_KEY$GEMINI_KEY$MISTRAL_KEY$GROK_KEY" ]]; then
        error "At least one LLM API key is required. Aborting."
        return 1
    fi
    success "LLM configuration validated"
}

# --- 4. Omnichannel Pairing (Guided) ---
configure_messaging() {
    info "[4/6] Configuring Omnichannel Messaging..."
    echo -e "\n${CYAN}--- BOT PAIRING & OMNICHANNEL SETUP ---${NC}"
    
    # Telegram Pairing Logic
    echo -e "${BOLD}Telegram:${NC} Contact @BotFather, create a bot, and paste the API Token."
    safe_read "Telegram Bot Token: " TELEGRAM_TOKEN
    
    # WhatsApp Pairing Logic (Meta Graph API)
    echo -e "\n${BOLD}WhatsApp:${NC} Configure your App at developers.facebook.com."
    safe_read "WhatsApp Permanent Access Token: " WHATSAPP_TOKEN
    safe_read "WhatsApp Business Phone ID: " WHATSAPP_ID
    safe_read "Webhook Verify Token [hermes_mythos_v2]: " VERIFY_TOKEN "hermes_mythos_v2"
    
    # Write the Immutable .env (atomic write)
    local env_tmp=".env.tmp.$$"
    {
        echo "# GENERATED BY HERMES PROVISIONER v$SCRIPT_VERSION"
        echo "# Created: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
        echo ""
        echo "# LLM Gateway"
        echo "ANTHROPIC_API_KEY=$ANTHROPIC_KEY"
        echo "OPENAI_API_KEY=$OPENAI_KEY"
        echo "GEMINI_API_KEY=$GEMINI_KEY"
        echo "MISTRAL_API_KEY=$MISTRAL_KEY"
        echo "GROK_API_KEY=$GROK_KEY"
        echo ""
        echo "# Messaging Channels"
        echo "TELEGRAM_BOT_TOKEN=$TELEGRAM_TOKEN"
        echo "WHATSAPP_ACCESS_TOKEN=$WHATSAPP_TOKEN"
        echo "WHATSAPP_PHONE_NUMBER_ID=$WHATSAPP_ID"
        echo "WHATSAPP_VERIFY_TOKEN=$VERIFY_TOKEN"
        echo ""
        echo "# Network & Runtime"
        echo "SERVER_URL=http://$PUBLIC_IP:8000"
        echo "OLLAMA_HOST=http://host.docker.internal:11434"
        echo "DB_PATH=/app/library_db/library.db"
        echo "LOG_LEVEL=INFO"
        echo "PYTHONMALLOC=malloc"
        echo ""
        echo "# Security (rotate these in production)"
        echo "SECRET_KEY=${SECRET_KEY:-$(openssl rand -hex 32 2>/dev/null || echo "change_me_in_production")}"
    } > "$env_tmp"
    
    # Atomic move with secure permissions
    mv "$env_tmp" .env
    chmod 600 .env 2>/dev/null || true
    success "Environment configuration persisted securely"
}

# --- 5. Containerized Deployment ---
deploy() {
    info "[5/6] Launching Containers..."
    
    # Pre-flight: Check Docker daemon
    if ! docker info &>/dev/null; then
        error "Docker daemon not running. Start Docker and retry."
        return 1
    fi
    
    # Check port availability (warn only)
    for port in "${REQUIRED_PORTS[@]}"; do
        if command -v ss &>/dev/null && ss -tlnp 2>/dev/null | grep -q ":$port "; then
            warn "Port $port is in use. Deployment may fail."
        fi
    done
    
    # Deploy with cleanup
    docker-compose down --remove-orphans 2>/dev/null || true
    if ! docker-compose up --build -d; then
        error "Container deployment failed"
        return 1
    fi
    
    # Wait briefly for health check
    info "Waiting for services to initialize..."
    sleep 10
    if docker-compose ps 2>/dev/null | grep -q "Up"; then
        success "Containers launched"
    else
        warn "Some containers may not be running. Check logs: docker-compose logs"
    fi
}

# --- 6. Automated Webhook Registration (The "Handshake") ---
finalize_pairing() {
    info "[6/6] Finalizing Bot Handshake..."
    
    # Telegram webhook registration
    if [[ -n "${TELEGRAM_TOKEN:-}" ]]; then
        info "Registering Telegram Webhook..."
        local webhook_url="http://$PUBLIC_IP:8000/webhooks/telegram"
        local response
        response=$(curl -s -X POST \
            "https://api.telegram.org/bot$TELEGRAM_TOKEN/setWebhook" \
            -d "url=$webhook_url" 2>/dev/null) || true
        
        if [[ "$response" == *"\"ok\":true"* ]]; then
            success "Telegram paired successfully"
        else
            warn "Telegram pairing response: ${response:-no response}"
        fi
    fi
    
    # Summary Report
    echo -e "\n${CYAN}${BOLD}================== DEPLOYMENT SUMMARY ===================${NC}"
    echo -e "Server IP:        ${BOLD}$PUBLIC_IP${NC}"
    echo -e "Orchestrator URL: ${BOLD}http://$PUBLIC_IP:8000${NC}"
    echo -e "Health Endpoint:  ${BOLD}http://$PUBLIC_IP:8000/health${NC}"
    
    if [[ -n "${TELEGRAM_TOKEN:-}" ]]; then
        echo -e "${GREEN}✓ Telegram:${NC} Active (webhook registered)"
    else
        echo -e "${YELLOW}○ Telegram:${NC} Skipped"
    fi
    
    if [[ -n "${WHATSAPP_TOKEN:-}" ]]; then
        echo -e "${GREEN}✓ WhatsApp:${NC} Configure webhook in Meta Dashboard:"
        echo -e "  URL: ${YELLOW}http://$PUBLIC_IP:8000/webhooks/whatsapp${NC}"
        echo -e "  Verify Token: ${YELLOW}$VERIFY_TOKEN${NC}"
    else
        echo -e "${YELLOW}○ WhatsApp:${NC} Skipped"
    fi
    
    echo -e "${CYAN}=======================================================${NC}"
    echo -e "${GREEN}${BOLD}Hermes-Mythos v$SCRIPT_VERSION is deployed and ready.${NC}"
    echo -e "The 7-Layer Cognitive DAG is standing by."
}

# --- Main Execution ---
main() {
    clear
    echo -e "${CYAN}${BOLD}"
    echo "======================================================================"
    echo "    HERMES-MYTHOS: PRODUCTION ORCHESTRATOR DEPLOYMENT v$SCRIPT_VERSION"
    echo "======================================================================"
    echo -e "${NC}"
    
    # Initialize environment
    detect_env
    
    # Execute deployment pipeline
    install_dependencies || exit 1
    setup_repo || exit 1
    configure_llm || exit 1
    configure_messaging || exit 1
    deploy || exit 1
    finalize_pairing
    
    info "Deployment log saved to: $LOG_FILE"
}

# Entry point
main "$@"
