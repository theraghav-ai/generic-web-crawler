#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"

activate_venv() {
    if [[ ! -d "$VENV_DIR" ]]; then
        python3 -m venv "$VENV_DIR"
    fi
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"
}

install_deps() {
    activate_venv
    python -m pip install --upgrade pip
    pip install -r "$SCRIPT_DIR/requirements.txt"
    playwright install chromium
}

cmd_setup() {
    install_deps
}

cmd_crawl() {
    activate_venv
    cd "$SCRIPT_DIR"
    python main.py crawl "$@"
}

cmd_status() {
    activate_venv
    cd "$SCRIPT_DIR"
    python main.py status "$@"
}

cmd_logs() {
    tail -n 100 -f "$SCRIPT_DIR/logs/crawler.log"
}

cmd_help() {
    cat <<EOF
Usage: ./manage.sh <command> [args]

Commands:
  setup                      Install dependencies and Playwright Chromium
  crawl <url> [options]      Run a crawl for the target website
  status [url]               Show the last-run metadata
  logs                       Tail the crawler log
  help                       Show this help

Examples:
  ./manage.sh setup
  ./manage.sh crawl https://www.dot.gov.in/ --max-pages 40 --max-depth 2
  ./manage.sh status https://www.dot.gov.in/
EOF
}

case "${1:-help}" in
    setup)
        shift
        cmd_setup "$@"
        ;;
    crawl)
        shift
        cmd_crawl "$@"
        ;;
    status)
        shift
        cmd_status "$@"
        ;;
    logs)
        shift
        cmd_logs "$@"
        ;;
    help|--help|-h)
        cmd_help
        ;;
    *)
        echo "Unknown command: $1"
        cmd_help
        exit 1
        ;;
esac
