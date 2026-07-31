#!/usr/bin/env bash
# Start IELTS Codex from a source checkout with Python 3.10 or later.
#
# The application has no runtime dependencies. This launcher finds a suitable
# interpreter, or asks the system package manager to install one when possible.

set -euo pipefail

readonly PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly MINIMUM_PYTHON="3.10"
PYTHON_EXECUTABLE=""

info() {
    printf 'IELTS Codex: %s\n' "$*"
}

die() {
    printf 'IELTS Codex: %s\n' "$*" >&2
    exit 1
}

resolve_command() {
    local candidate="$1"

    if [[ -x "$candidate" ]]; then
        printf '%s\n' "$candidate"
        return 0
    fi
    command -v "$candidate" 2>/dev/null
}

is_compatible_python() {
    local candidate="$1"

    "$candidate" -c \
        'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' \
        >/dev/null 2>&1
}

use_python_if_compatible() {
    local requested="$1"
    local resolved=""

    if ! resolved="$(resolve_command "$requested")"; then
        return 1
    fi
    if ! is_compatible_python "$resolved"; then
        return 1
    fi

    PYTHON_EXECUTABLE="$resolved"
    return 0
}

find_python() {
    local candidate=""

    if [[ -n "${IELTS_CODEX_PYTHON:-}" ]]; then
        if use_python_if_compatible "$IELTS_CODEX_PYTHON"; then
            return 0
        fi
        die "IELTS_CODEX_PYTHON must name a Python ${MINIMUM_PYTHON}+ interpreter."
    fi

    for candidate in python3 python3.10 python3.11 python3.12 python3.13 python3.14; do
        if use_python_if_compatible "$candidate"; then
            return 0
        fi
    done
    return 1
}

run_privileged() {
    if [[ "$(id -u)" -eq 0 ]]; then
        "$@"
        return
    fi
    if command -v sudo >/dev/null 2>&1; then
        sudo "$@"
        return
    fi
    die "Installing Python needs administrator access, but sudo is unavailable."
}

apt_package_available() {
    local package="$1"

    # apt-cache show accepts regular expressions. Its literal package-name list
    # plus grep's fixed, whole-line match avoids regex fallback entirely.
    apt-cache pkgnames | grep -Fqx -- "$package"
}

refresh_apt_cache() {
    if ! run_privileged apt-get update; then
        info "APT update reported an error from another configured repository; continuing with cached package metadata. Repair that repository before your next normal system update."
    fi
}

install_supported_apt_python() {
    local package=""

    for package in python3.10 python3.11 python3.12 python3.13 python3.14; do
        if apt_package_available "$package"; then
            info "Installing ${package}..."
            run_privileged apt-get install -y "$package"
            return 0
        fi
    done
    return 1
}

is_ubuntu() {
    local distribution_id=""

    if [[ -r /etc/os-release ]]; then
        distribution_id="$(. /etc/os-release; printf '%s' "${ID:-}")"
    fi
    [[ "$distribution_id" == "ubuntu" ]]
}

confirm_deadsnakes_ppa() {
    local answer=""

    if [[ ! -t 0 ]]; then
        info "Ubuntu's optional Python PPA needs an interactive confirmation."
        return 1
    fi
    printf '%s' \
        'IELTS Codex: No compatible Python package is available. Add the third-party ppa:deadsnakes/ppa source to install one? [y/N] ' \
        >&2
    if ! IFS= read -r answer; then
        return 1
    fi
    case "$answer" in
        [yY] | [yY][eE][sS]) return 0 ;;
        *) return 1 ;;
    esac
}

install_from_deadsnakes_ppa() {
    if ! is_ubuntu || ! confirm_deadsnakes_ppa; then
        return 1
    fi
    if ! command -v add-apt-repository >/dev/null 2>&1; then
        info "Installing add-apt-repository from the configured Ubuntu repositories..."
        run_privileged apt-get install -y software-properties-common
    fi
    info "Adding ppa:deadsnakes/ppa..."
    if ! run_privileged add-apt-repository -y ppa:deadsnakes/ppa; then
        info "The PPA command reported an update error; checking its cached package metadata."
    fi
    refresh_apt_cache
    install_supported_apt_python
}

install_with_apt() {
    info "No compatible Python found; checking APT for Python ${MINIMUM_PYTHON}+..."
    refresh_apt_cache
    if install_supported_apt_python; then
        return
    fi
    if install_from_deadsnakes_ppa; then
        return
    fi
    die "Your configured APT repositories do not provide Python ${MINIMUM_PYTHON}+.
Install a compatible Python version, set IELTS_CODEX_PYTHON, or rerun ./run.sh
and approve the optional Ubuntu PPA when prompted."
}

install_with_dnf() {
    info "No compatible Python found; installing the distribution Python with DNF..."
    run_privileged dnf install -y python3
}

install_with_pacman() {
    info "No compatible Python found; installing Python with pacman..."
    run_privileged pacman -S --needed --noconfirm python
}

install_with_homebrew() {
    info "No compatible Python found; installing Python with Homebrew..."
    brew install python
}

install_python() {
    if command -v apt-get >/dev/null 2>&1; then
        install_with_apt
        return
    fi
    if command -v dnf >/dev/null 2>&1; then
        install_with_dnf
        return
    fi
    if command -v pacman >/dev/null 2>&1; then
        install_with_pacman
        return
    fi
    if command -v brew >/dev/null 2>&1; then
        install_with_homebrew
        return
    fi
    die "No supported package manager was found. Install Python ${MINIMUM_PYTHON}+,
or set IELTS_CODEX_PYTHON to its executable path."
}

if ! find_python; then
    if [[ "${IELTS_CODEX_NO_AUTO_INSTALL:-}" == "1" ]]; then
        die "Python ${MINIMUM_PYTHON}+ is required. Set IELTS_CODEX_PYTHON or install it first."
    fi
    install_python
    if ! find_python; then
        die "Python installation finished, but no Python ${MINIMUM_PYTHON}+ interpreter was found.
Set IELTS_CODEX_PYTHON to the installed executable and run ./run.sh again."
    fi
fi

info "Using $("$PYTHON_EXECUTABLE" -c 'import sys; print(sys.version.split()[0])')."
exec "$PYTHON_EXECUTABLE" "$PROJECT_ROOT/ielts.py" "$@"
