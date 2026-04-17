#!/usr/bin/env bash
# ============================================================
# Rival Plugin — Azure DevOps Setup
# ============================================================
# This script guides users through:
#   1. Creating/entering their Azure DevOps PAT
#   2. Configuring organization + project
#   3. Testing the connection
#   4. Pulling all repos + wikis into a knowledge folder
#   5. Writing a .env file for future use
#
# Compatibility: bash 3.2+ (macOS default), zsh, Linux bash
# Windows users: use the Python script directly or WSL.
#
# Usage:
#   bash setup-devops.sh                     # Interactive setup
#   bash setup-devops.sh --test              # Test existing .env connection
#   bash setup-devops.sh --refresh           # Re-pull repos/wikis with existing .env
#   bash setup-devops.sh --output-dir PATH   # Custom output directory
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
EXPORT_SCRIPT="${SCRIPT_DIR}/export-ado-knowledge.py"
DEFAULT_OUTPUT_DIR="./knowledge"
PYTHON_CMD=""

# Colors — disabled when not a terminal
if [ -t 1 ]; then
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

print_banner() {
    printf "\n"
    printf "%b%b======================================================%b\n" "$CYAN" "$BOLD" "$NC"
    printf "%b%b         Rival — Azure DevOps Setup                   %b\n" "$CYAN" "$BOLD" "$NC"
    printf "%b%b======================================================%b\n" "$CYAN" "$BOLD" "$NC"
    printf "\n"
}

log_info()    { printf "%b[rival]%b %s\n" "$BLUE" "$NC" "$1"; }
log_success() { printf "%b[rival]%b %s\n" "$GREEN" "$NC" "$1"; }
log_warn()    { printf "%b[rival]%b %s\n" "$YELLOW" "$NC" "$1"; }
log_error()   { printf "%b[rival]%b %s\n" "$RED" "$NC" "$1"; }

# Check Python 3 is available
check_python() {
    if command -v python3 >/dev/null 2>&1; then
        PYTHON_CMD="python3"
    elif command -v python >/dev/null 2>&1; then
        # Verify it's Python 3
        local ver
        ver="$(python --version 2>&1)"
        case "$ver" in
            Python\ 3*) PYTHON_CMD="python" ;;
            *)
                log_error "Found Python but it is not Python 3: $ver"
                log_error "Install Python 3: https://www.python.org/downloads/"
                exit 1
                ;;
        esac
    else
        log_error "Python 3 is required but not found."
        log_error "Install Python 3: https://www.python.org/downloads/"
        exit 1
    fi
}

# Check the export script exists
check_export_script() {
    if [ ! -f "$EXPORT_SCRIPT" ]; then
        log_error "Export script not found at: $EXPORT_SCRIPT"
        log_error "Make sure the Rival plugin is installed correctly."
        exit 1
    fi
}

# Load existing .env if present
load_env() {
    local env_file="${1:-.env}"
    if [ ! -f "$env_file" ]; then
        return 1
    fi
    log_info "Loading existing config from $env_file"
    while IFS='=' read -r key value; do
        # Skip comments and empty lines
        case "$key" in
            \#*|"") continue ;;
        esac
        # Strip surrounding quotes from value
        value="${value%\"}"
        value="${value#\"}"
        value="${value%\'}"
        value="${value#\'}"
        # Only export ADO_ prefixed vars
        case "$key" in
            ADO_*)
                export "${key}=${value}"
                ;;
        esac
    done < "$env_file"
    return 0
}

# Guide PAT creation
show_pat_guide() {
    printf "\n"
    printf "%bHow to create a Personal Access Token (PAT):%b\n" "$BOLD" "$NC"
    printf "\n"
    printf "  1. Go to your Azure DevOps organization\n"
    printf "     https://dev.azure.com/{your-org}/_usersSettings/tokens\n"
    printf "\n"
    printf "  2. Click 'New Token'\n"
    printf "\n"
    printf "  3. Configure the token:\n"
    printf "     - Name:         Rival Plugin\n"
    printf "     - Organization: Select your organization\n"
    printf "     - Expiration:   Custom (set to max allowed)\n"
    printf "     - Scopes:       Select these scopes:\n"
    printf "\n"
    printf "       %bCode%b          — Read\n" "$GREEN" "$NC"
    printf "       %bWiki%b          — Read\n" "$GREEN" "$NC"
    printf "       %bWork Items%b    — Read & Write  (optional, for board integration)\n" "$GREEN" "$NC"
    printf "       %bBuild%b         — Read           (optional, for pipeline status)\n" "$GREEN" "$NC"
    printf "\n"
    printf "  4. Click 'Create' and copy the token immediately\n"
    printf "     (you won't be able to see it again)\n"
    printf "\n"
}

# to_lower — portable lowercase (works on bash 3.2, zsh, etc.)
to_lower() {
    printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

# Interactive setup
interactive_setup() {
    local output_dir="${1:-$DEFAULT_OUTPUT_DIR}"

    print_banner

    # Check if .env already exists
    if load_env; then
        log_success "Existing configuration found:"
        printf "  Organization: %s\n" "${ADO_ORG:-not set}"
        printf "  Project:      %s\n" "${ADO_PROJECT:-not set}"
        if [ -n "${ADO_PAT:-}" ]; then
            printf "  PAT:          configured (%d chars)\n" "${#ADO_PAT}"
        else
            printf "  PAT:          not set\n"
        fi
        printf "\n"
        printf "Use existing configuration? [Y/n] "
        read -r use_existing
        if [ "$(to_lower "${use_existing:-y}")" != "n" ]; then
            log_info "Using existing configuration."
            run_export "$output_dir"
            return
        fi
        printf "\n"
    fi

    # Step 1: PAT
    log_info "Step 1/3 — Personal Access Token"
    printf "\n"

    if [ -n "${ADO_PAT:-}" ]; then
        printf "PAT already set (%d chars). Keep it? [Y/n] " "${#ADO_PAT}"
        read -r keep_pat
        if [ "$(to_lower "${keep_pat:-y}")" = "n" ]; then
            unset ADO_PAT
        fi
    fi

    if [ -z "${ADO_PAT:-}" ]; then
        printf "Do you have an Azure DevOps PAT? [y/N] "
        read -r has_pat
        if [ "$(to_lower "${has_pat:-n}")" != "y" ]; then
            show_pat_guide
            printf "%bAfter creating the token, paste it below.%b\n" "$YELLOW" "$NC"
        fi
        printf "\n"
        printf "Paste your PAT (input is hidden): "
        read -rs ADO_PAT
        printf "\n"
        if [ -z "$ADO_PAT" ]; then
            log_error "PAT cannot be empty."
            exit 1
        fi
        export ADO_PAT
    fi

    # Step 2: Organization
    printf "\n"
    log_info "Step 2/3 — Organization & Project"
    printf "\n"

    if [ -z "${ADO_ORG:-}" ]; then
        printf "  Your Azure DevOps URL looks like:\n"
        printf "  https://dev.azure.com/{organization}/{project}\n"
        printf "\n"
        printf "Organization name (e.g. rivalitinc): "
        read -r ADO_ORG
        if [ -z "$ADO_ORG" ]; then
            log_error "Organization cannot be empty."
            exit 1
        fi
        export ADO_ORG
    else
        log_info "Organization: $ADO_ORG"
    fi

    # Step 3: Project
    if [ -z "${ADO_PROJECT:-}" ]; then
        printf "Project name (e.g. Rival Insurance Technology): "
        read -r ADO_PROJECT
        if [ -z "$ADO_PROJECT" ]; then
            log_error "Project cannot be empty."
            exit 1
        fi
        export ADO_PROJECT
    else
        log_info "Project: $ADO_PROJECT"
    fi

    # Test connection
    printf "\n"
    log_info "Step 3/3 — Testing connection..."
    if ! "$PYTHON_CMD" "$EXPORT_SCRIPT" --test-connection; then
        log_error "Connection failed. Please check your PAT, organization, and project name."
        exit 1
    fi
    log_success "Connection verified!"

    # Save .env
    printf "\n"
    printf "Save configuration to .env for future use? [Y/n] "
    read -r save_env_answer
    if [ "$(to_lower "${save_env_answer:-y}")" != "n" ]; then
        write_env_file
        # Restrict permissions — owner read/write only
        chmod 600 .env
        log_success "Configuration saved to .env (permissions: owner-only)"
        log_warn "IMPORTANT: .env contains your PAT. Never commit it to git."

        # Add to .gitignore if not already there
        if [ -f .gitignore ]; then
            if ! grep -qxF '.env' .gitignore; then
                printf '.env\n' >> .gitignore
                log_info "Added .env to .gitignore"
            fi
        else
            printf '.env\n' > .gitignore
            log_info "Created .gitignore with .env entry"
        fi
    fi

    # Run export
    printf "\n"
    run_export "$output_dir"
}

write_env_file() {
    # Use single-quoted heredoc to prevent shell expansion of PAT contents
    # Then substitute variables with printf after
    printf '# Rival Plugin — Azure DevOps Configuration\n' > .env
    printf '# Generated: %s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date)" >> .env
    printf '# DO NOT commit this file to git.\n\n' >> .env
    printf 'ADO_PAT=%s\n' "$ADO_PAT" >> .env
    printf 'ADO_ORG=%s\n' "$ADO_ORG" >> .env
    printf 'ADO_PROJECT=%s\n' "$ADO_PROJECT" >> .env
    printf 'ADO_OUTPUT_DIR=%s\n' "${ADO_OUTPUT_DIR:-knowledge}" >> .env
}

run_export() {
    local output_dir="${1:-$DEFAULT_OUTPUT_DIR}"

    printf "\n"
    log_info "Pulling Azure DevOps content..."
    printf "  - Cloning all repositories\n"
    printf "  - Exporting all wikis\n"
    printf "  - Output: %s\n" "$output_dir"
    printf "\n"

    export ADO_OUTPUT_DIR="$output_dir"

    if "$PYTHON_CMD" "$EXPORT_SCRIPT" --output-dir "$output_dir"; then
        printf "\n"
        log_success "Azure DevOps knowledge export complete!"
        printf "\n"
        printf "%bWhat was created:%b\n" "$BOLD" "$NC"
        printf "  %s/\n" "$output_dir"
        printf "    repos/          — All cloned repositories\n"
        printf "    wikis/          — All exported wiki content\n"
        printf "    summary.json    — Index of everything downloaded\n"
        printf "\n"
        printf "%bNext step:%b\n" "$BOLD" "$NC"
        printf "  Run /rival:rival-init to index everything and start planning.\n"
        printf "\n"
    else
        log_error "Export failed. Check the error messages above."
        exit 1
    fi
}

# ============================================================
# Main
# ============================================================

check_python
check_export_script

OUTPUT_DIR="$DEFAULT_OUTPUT_DIR"
MODE="interactive"

while [ $# -gt 0 ]; do
    case $1 in
        --test)
            MODE="test"
            shift
            ;;
        --refresh)
            MODE="refresh"
            shift
            ;;
        --output-dir)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --help|-h)
            printf "Usage: setup-devops.sh [OPTIONS]\n"
            printf "\n"
            printf "Options:\n"
            printf "  --test              Test existing .env connection\n"
            printf "  --refresh           Re-pull repos/wikis with existing .env\n"
            printf "  --output-dir PATH   Custom output directory (default: ./knowledge)\n"
            printf "  --help              Show this help\n"
            printf "\n"
            printf "Windows users: run the Python script directly instead:\n"
            printf "  python scripts/export-ado-knowledge.py --test-connection\n"
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

case "$MODE" in
    test)
        if ! load_env; then
            log_error "No .env file found in $(pwd). Run setup-devops.sh first."
            exit 1
        fi
        log_info "Testing connection..."
        "$PYTHON_CMD" "$EXPORT_SCRIPT" --test-connection
        ;;
    refresh)
        if ! load_env; then
            log_error "No .env file found in $(pwd). Run setup-devops.sh first."
            exit 1
        fi
        run_export "$OUTPUT_DIR"
        ;;
    interactive)
        interactive_setup "$OUTPUT_DIR"
        ;;
esac
