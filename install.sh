#!/usr/bin/env bash
# Install a user-level `ielts` command that follows this source checkout.

set -euo pipefail

readonly PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly DEFAULT_INSTALL_DIR="${HOME:?IELTS Codex needs HOME to install a user command.}/.local/bin"
readonly INSTALL_DIR="${IELTS_CODEX_INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"
readonly COMMAND_PATH="$INSTALL_DIR/ielts"
readonly COMMAND_MARKER="# IELTS Codex source launcher"

info() {
    printf 'IELTS Codex: %s\n' "$*"
}

die() {
    printf 'IELTS Codex: %s\n' "$*" >&2
    exit 1
}

if [[ -e "$COMMAND_PATH" || -L "$COMMAND_PATH" ]]; then
    if [[ -L "$COMMAND_PATH" ]] || ! grep -Fq -- "$COMMAND_MARKER" "$COMMAND_PATH" 2>/dev/null; then
        die "Refusing to replace an unrelated command at $COMMAND_PATH."
    fi
fi

info "Checking the Python 3.10+ launcher before installing the command..."
"$PROJECT_ROOT/run.sh" --version

mkdir -p "$INSTALL_DIR"
temporary_command="$(mktemp "$INSTALL_DIR/.ielts.XXXXXX")"
cleanup() {
    if [[ -n "${temporary_command:-}" && -e "$temporary_command" ]]; then
        rm -f -- "$temporary_command"
    fi
}
trap cleanup EXIT

{
    printf '%s\n' '#!/usr/bin/env bash' "$COMMAND_MARKER"
    printf 'exec %q "$@"\n' "$PROJECT_ROOT/run.sh"
} >"$temporary_command"
chmod 755 "$temporary_command"
mv -f -- "$temporary_command" "$COMMAND_PATH"
temporary_command=""
info "Installed the ielts command at $COMMAND_PATH."

if [[ -n "${IELTS_CODEX_INSTALL_DIR:-}" ]]; then
    info "Custom install directory used; add $INSTALL_DIR to PATH if needed."
    exit 0
fi

case ":${PATH:-}:" in
    *":$INSTALL_DIR:"*)
        info "Run 'ielts' to open the interface."
        exit 0
        ;;
esac

shell_name="${SHELL##*/}"
case "$shell_name" in
    zsh) profile="$HOME/.zshrc" ;;
    bash)
        if [[ "$(uname -s)" == "Darwin" ]]; then
            profile="$HOME/.bash_profile"
        else
            profile="$HOME/.bashrc"
        fi
        ;;
    *) profile="$HOME/.profile" ;;
esac
path_line='export PATH="$HOME/.local/bin:$PATH"'
if ! grep -Fqx -- "$path_line" "$profile" 2>/dev/null; then
    if ! printf '\n# IELTS Codex command\n%s\n' "$path_line" >>"$profile"; then
        info "Could not update $profile; add $INSTALL_DIR to PATH manually."
        exit 0
    fi
fi
info "Added $INSTALL_DIR to PATH in $profile."
info "Open a new terminal, then run 'ielts'."
