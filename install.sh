#!/usr/bin/env bash
# Ghost Desk — macOS and Linux installer.
# Paste:
#   curl -fsSL https://raw.githubusercontent.com/HassanForged/ghost-desk/main/install.sh | bash
# Does not need Homebrew or a new system Python. Macs on 3.9 are fine.
set -euo pipefail

REPO="${GHOST_DESK_REPO:-https://github.com/HassanForged/ghost-desk.git}"
REF="${GHOST_DESK_REF:-main}"
PREFIX="${GHOST_HOME:-$HOME/.local/share/ghost-desk}"
BIN_DIR="${GHOST_BIN:-$HOME/.local/bin}"

fail() {
  printf 'ghost-desk install: %s\n' "$*" >&2
  exit 1
}

info() {
  printf 'ghost-desk install: %s\n' "$*"
}

os_name="$(uname -s 2>/dev/null || true)"
case "$os_name" in
  Darwin|Linux) ;;
  MINGW*|MSYS*|CYGWIN*)
    fail "this script is for Mac and Linux. On Windows: python -m venv .venv && .venv\\Scripts\\pip install -e ."
    ;;
  *)
    fail "unsupported OS: ${os_name:-unknown}"
    ;;
esac

command -v curl >/dev/null 2>&1 || fail "curl is required"
command -v git >/dev/null 2>&1 || fail "git is required (mac: xcode-select --install / linux: install git)"

export PATH="$BIN_DIR:$HOME/.cargo/bin:$PATH"

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    return 0
  fi
  info "installing uv so Ghost Desk can use Python 3.12 without Homebrew"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$BIN_DIR:$HOME/.cargo/bin:$PATH"
  command -v uv >/dev/null 2>&1 || fail "uv did not land on PATH. Open a new terminal and run the install line again."
}

mkdir -p "$PREFIX" "$BIN_DIR"
ensure_uv

if [ -d "$PREFIX/.git" ]; then
  info "updating $PREFIX"
  git -C "$PREFIX" fetch --depth 1 origin "$REF"
  git -C "$PREFIX" checkout --force FETCH_HEAD >/dev/null
else
  info "cloning $REPO ($REF)"
  rm -rf "$PREFIX"
  git clone --depth 1 --branch "$REF" "$REPO" "$PREFIX"
fi

info "installing Python 3.12 for Ghost Desk only (leaves system Python alone)"
uv python install 3.12
info "creating venv"
uv venv --python 3.12 "$PREFIX/.venv"
info "installing ghost"
uv pip install --python "$PREFIX/.venv/bin/python" -e "$PREFIX"

ln -sfn "$PREFIX/.venv/bin/ghost" "$BIN_DIR/ghost"

path_line='export PATH="$HOME/.local/bin:$PATH"'
if ! echo ":$PATH:" | grep -q ":$BIN_DIR:"; then
  for rc in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.profile"; do
    if [ -f "$rc" ] && ! grep -F "$BIN_DIR" "$rc" >/dev/null 2>&1; then
      printf '\n# Ghost Desk\n%s\n' "$path_line" >> "$rc"
      info "added $BIN_DIR to $rc"
    fi
  done
  if command -v fish >/dev/null 2>&1 && [ -d "$HOME/.config/fish" ]; then
    mkdir -p "$HOME/.config/fish"
    if [ ! -f "$HOME/.config/fish/config.fish" ] || ! grep -F "$BIN_DIR" "$HOME/.config/fish/config.fish" >/dev/null 2>&1; then
      printf '\n# Ghost Desk\nfish_add_path %s\n' "$BIN_DIR" >> "$HOME/.config/fish/config.fish"
    fi
  fi
  export PATH="$BIN_DIR:$PATH"
fi

[ -x "$BIN_DIR/ghost" ] || fail "ghost binary missing at $BIN_DIR/ghost"

info "ok  $("$BIN_DIR/ghost" --version 2>/dev/null || echo ghost-desk)"
printf '\nNext (new terminal if needed):\n  ghost\n\nIf ghost is not found:\n  export PATH="$HOME/.local/bin:$PATH"\n  ghost\n\n'
