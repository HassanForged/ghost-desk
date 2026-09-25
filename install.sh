#!/usr/bin/env bash
# Ghost Desk — macOS and Linux installer.
# Paste:
#   curl -fsSL https://raw.githubusercontent.com/HassanForged/ghost-desk/main/install.sh | bash
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

command -v git >/dev/null 2>&1 || fail "git is required (mac: xcode-select --install / linux: install git)"

pick_python() {
  local candidate version
  for candidate in "${GHOST_PYTHON:-}" python3.13 python3.12 python3.11 python3; do
    [ -n "$candidate" ] || continue
    command -v "$candidate" >/dev/null 2>&1 || continue
    version="$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
    case "$version" in
      3.1[1-9]|3.[2-9][0-9]) echo "$candidate"; return 0 ;;
    esac
  done
  return 1
}

PYTHON="$(pick_python)" || fail "Python 3.11 or newer is required. mac: brew install python@3.12   linux: install python3.12 and python3.12-venv"

mkdir -p "$PREFIX" "$BIN_DIR"

if [ -d "$PREFIX/.git" ]; then
  info "updating $PREFIX"
  git -C "$PREFIX" fetch --depth 1 origin "$REF"
  git -C "$PREFIX" checkout --force FETCH_HEAD >/dev/null
else
  info "cloning $REPO ($REF)"
  rm -rf "$PREFIX"
  git clone --depth 1 --branch "$REF" "$REPO" "$PREFIX"
fi

info "using $PYTHON"
"$PYTHON" -m venv "$PREFIX/.venv"
# shellcheck disable=SC1091
. "$PREFIX/.venv/bin/activate"
python -m pip install --upgrade pip
python -m pip install -e "$PREFIX"

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

command -v ghost >/dev/null 2>&1 || fail "ghost is not on PATH yet. Open a new terminal, or run: export PATH=\"$BIN_DIR:\$PATH\""

info "ok  $($BIN_DIR/ghost --version 2>/dev/null || echo ghost-desk)"
printf '\nNext:\n  ghost\n\n'
