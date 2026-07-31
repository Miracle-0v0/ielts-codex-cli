#!/usr/bin/env bash
# Start IELTS Codex from a source checkout with Python 3.10 or later.
#
# The application has no runtime dependencies. This launcher finds a suitable
# interpreter or offers an isolated, project-local Python managed by Astral uv.

set -euo pipefail

readonly PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly MINIMUM_PYTHON="3.10"
readonly MANAGED_PYTHON="3.12"
readonly UV_INSTALL_VERSION="0.11.32"
readonly BOOTSTRAP_ROOT="${IELTS_CODEX_BOOTSTRAP_DIR:-$PROJECT_ROOT/.ielts-bootstrap}"
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

find_project_managed_python() {
    local candidate=""

    for candidate in \
        "$BOOTSTRAP_ROOT"/python/*/bin/"python${MANAGED_PYTHON}" \
        "$BOOTSTRAP_ROOT"/python/*/bin/python; do
        if [[ -x "$candidate" ]] && is_compatible_python "$candidate"; then
            PYTHON_EXECUTABLE="$candidate"
            return 0
        fi
    done
    return 1
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
    if find_project_managed_python; then
        return 0
    fi
    return 1
}

confirm_managed_python() {
    local answer=""

    if [[ ! -t 0 ]]; then
        info "The managed Python fallback needs an interactive confirmation."
        return 1
    fi
    printf 'IELTS Codex: No Python %s+ interpreter was found. Use Astral uv (downloading pinned uv if needed) to install project-local Python %s into %s? This will not change the system Python or shell PATH. [y/N] ' \
        "$MINIMUM_PYTHON" \
        "$MANAGED_PYTHON" \
        "$BOOTSTRAP_ROOT" \
        >&2
    if ! IFS= read -r answer; then
        return 1
    fi
    case "$answer" in
        [yY] | [yY][eE][sS]) return 0 ;;
        *) return 1 ;;
    esac
}

download_uv() {
    local installer_url="https://astral.sh/uv/${UV_INSTALL_VERSION}/install.sh"
    local installer_file=""
    local uv_directory="$BOOTSTRAP_ROOT/bin"
    local uv_executable="$uv_directory/uv"

    mkdir -p "$uv_directory"
    installer_file="$(mktemp "${TMPDIR:-/tmp}/ielts-codex-uv.XXXXXX")"
    if command -v curl >/dev/null 2>&1; then
        if ! curl -LsSf "$installer_url" -o "$installer_file"; then
            rm -f -- "$installer_file"
            die "Could not download the pinned uv installer from ${installer_url}."
        fi
    elif command -v wget >/dev/null 2>&1; then
        if ! wget -qO "$installer_file" "$installer_url"; then
            rm -f -- "$installer_file"
            die "Could not download the pinned uv installer from ${installer_url}."
        fi
    else
        rm -f -- "$installer_file"
        die "The managed Python fallback requires curl or wget."
    fi

    info "Installing uv ${UV_INSTALL_VERSION} inside the project bootstrap directory..." >&2
    if ! UV_UNMANAGED_INSTALL="$uv_directory" UV_NO_MODIFY_PATH=1 \
        sh "$installer_file" >&2; then
        rm -f -- "$installer_file"
        die "The pinned uv installer failed."
    fi
    rm -f -- "$installer_file"
    if [[ ! -x "$uv_executable" ]]; then
        die "uv installation completed, but its executable was not found."
    fi
    printf '%s\n' "$uv_executable"
}

install_managed_python() {
    local uv_executable=""
    local managed_python=""
    local python_directory="$BOOTSTRAP_ROOT/python"
    local cache_directory="$BOOTSTRAP_ROOT/cache"

    if ! confirm_managed_python; then
        return 1
    fi
    if uv_executable="$(resolve_command uv)"; then
        info "Using existing uv executable at ${uv_executable}."
    elif [[ -x "$BOOTSTRAP_ROOT/bin/uv" ]]; then
        uv_executable="$BOOTSTRAP_ROOT/bin/uv"
    else
        if ! uv_executable="$(download_uv)"; then
            return 1
        fi
    fi

    mkdir -p "$python_directory" "$cache_directory"
    info "Installing project-local Python ${MANAGED_PYTHON} with uv..."
    if ! UV_CACHE_DIR="$cache_directory" \
        UV_PYTHON_INSTALL_DIR="$python_directory" \
        UV_PYTHON_INSTALL_BIN=false \
        "$uv_executable" python install "$MANAGED_PYTHON"; then
        return 1
    fi
    if ! managed_python="$(
        UV_CACHE_DIR="$cache_directory" \
            UV_PYTHON_INSTALL_DIR="$python_directory" \
            "$uv_executable" python find "$MANAGED_PYTHON"
    )"; then
        return 1
    fi
    if ! is_compatible_python "$managed_python"; then
        return 1
    fi
    PYTHON_EXECUTABLE="$managed_python"
    return 0
}

if ! find_python; then
    if [[ "${IELTS_CODEX_NO_AUTO_INSTALL:-}" == "1" ]]; then
        die "Python ${MINIMUM_PYTHON}+ is required. Set IELTS_CODEX_PYTHON or install it first."
    fi
    if ! install_managed_python; then
        die "Python ${MINIMUM_PYTHON}+ is required. Install it yourself, set
IELTS_CODEX_PYTHON, or rerun ./run.sh interactively and approve the uv-managed
project-local Python."
    fi
    if ! find_python; then
        die "uv finished, but no Python ${MINIMUM_PYTHON}+ interpreter was found."
    fi
fi

info "Using $("$PYTHON_EXECUTABLE" -c 'import sys; print(sys.version.split()[0])')."
exec "$PYTHON_EXECUTABLE" "$PROJECT_ROOT/ielts.py" "$@"
