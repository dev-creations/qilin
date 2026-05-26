#!/usr/bin/env sh
# Qilin installer for macOS and Linux.
#
# Usage:
#     curl -fsSL https://raw.githubusercontent.com/dev-creations/qilin/main/scripts/install.sh | sh
#
# Optional environment variables:
#     QILIN_VERSION   - version to install (default: latest GitHub release).
#     QILIN_PREFIX    - install prefix (default: /usr/local; falls back to $HOME/.local without sudo).
#     QILIN_REPO      - GitHub repo (default: dev-creations/qilin).
#
# The script downloads the matching release tarball, verifies its SHA-256
# against the published checksums.txt, and installs `qilin` into $QILIN_PREFIX/bin.

set -eu

REPO="${QILIN_REPO:-dev-creations/qilin}"
PREFIX="${QILIN_PREFIX:-}"
VERSION="${QILIN_VERSION:-}"

info()    { printf 'qilin-install: %s\n' "$*" >&2; }
warn()    { printf 'qilin-install: warning: %s\n' "$*" >&2; }
die()     { printf 'qilin-install: error: %s\n' "$*" >&2; exit 1; }

need() { command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"; }

detect_os() {
    uname_s=$(uname -s 2>/dev/null || echo "")
    case "$uname_s" in
        Darwin) echo "darwin" ;;
        Linux)  echo "linux" ;;
        *) die "unsupported OS: $uname_s (use the Windows installer instead)" ;;
    esac
}

detect_arch() {
    uname_m=$(uname -m 2>/dev/null || echo "")
    case "$uname_m" in
        x86_64|amd64) echo "x86_64" ;;
        arm64|aarch64) echo "arm64" ;;
        *) die "unsupported architecture: $uname_m" ;;
    esac
}

# resolve_version queries GitHub for the latest tag unless one was set.
resolve_version() {
    if [ -n "$VERSION" ]; then
        echo "$VERSION"; return
    fi
    api="https://api.github.com/repos/${REPO}/releases/latest"
    tag=$(curl -fsSL "$api" \
        | grep -m1 '"tag_name"' \
        | sed -E 's/.*"tag_name": *"([^"]+)".*/\1/')
    [ -n "$tag" ] || die "could not determine latest release tag from $api"
    echo "$tag"
}

pick_prefix() {
    if [ -n "$PREFIX" ]; then
        echo "$PREFIX"; return
    fi
    if [ -w /usr/local ] 2>/dev/null; then
        echo "/usr/local"; return
    fi
    if [ "$(id -u)" = "0" ]; then
        echo "/usr/local"; return
    fi
    if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
        echo "/usr/local"; return
    fi
    echo "${HOME}/.local"
}

sha256_of() {
    file="$1"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$file" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$file" | awk '{print $1}'
    else
        die "neither sha256sum nor shasum is available; install one and retry"
    fi
}

main() {
    need curl
    need tar

    os=$(detect_os)
    arch=$(detect_arch)
    version=$(resolve_version)
    version_num=${version#v}
    prefix=$(pick_prefix)

    info "OS=$os ARCH=$arch VERSION=$version PREFIX=$prefix"

    tmp=$(mktemp -d 2>/dev/null || mktemp -d -t qilin-install)
    trap 'rm -rf "$tmp"' EXIT INT TERM

    archive_name="qilin_${version_num}_${os}_${arch}.tar.gz"
    archive_url="https://github.com/${REPO}/releases/download/${version}/${archive_name}"
    checksums_url="https://github.com/${REPO}/releases/download/${version}/checksums.txt"

    info "downloading $archive_url"
    curl -fsSL --retry 3 -o "${tmp}/${archive_name}" "$archive_url" \
        || die "failed to download $archive_url"

    info "downloading checksums"
    curl -fsSL --retry 3 -o "${tmp}/checksums.txt" "$checksums_url" \
        || die "failed to download checksums.txt"

    expected=$(grep "  ${archive_name}$" "${tmp}/checksums.txt" | awk '{print $1}')
    [ -n "$expected" ] || die "no checksum entry for $archive_name in checksums.txt"
    actual=$(sha256_of "${tmp}/${archive_name}")
    if [ "$expected" != "$actual" ]; then
        die "SHA-256 mismatch: expected $expected, got $actual"
    fi
    info "checksum verified ($actual)"

    tar -xzf "${tmp}/${archive_name}" -C "$tmp"
    bin_src="${tmp}/qilin"
    [ -x "$bin_src" ] || die "extracted archive does not contain a qilin binary"

    bin_dir="${prefix}/bin"
    mkdir -p "$bin_dir" 2>/dev/null || sudo mkdir -p "$bin_dir"

    if [ -w "$bin_dir" ]; then
        install -m 0755 "$bin_src" "${bin_dir}/qilin"
    else
        info "writing to ${bin_dir}/qilin (requires sudo)"
        sudo install -m 0755 "$bin_src" "${bin_dir}/qilin"
    fi

    # On macOS, `curl` (and the `tar` step above) inherit the
    # `com.apple.quarantine` xattr from the downloaded tarball. Left in
    # place, Gatekeeper would refuse to launch the binary with the
    # "Apple cannot verify..." dialog on first run. The release build
    # ad-hoc signs darwin binaries via rcodesign, so removing quarantine
    # here is the second half of getting `qilin --version` to work
    # cleanly without an Apple Developer ID + notarization.
    if [ "$os" = "darwin" ] && command -v xattr >/dev/null 2>&1; then
        if [ -w "${bin_dir}/qilin" ]; then
            xattr -d com.apple.quarantine "${bin_dir}/qilin" 2>/dev/null || true
        else
            sudo xattr -d com.apple.quarantine "${bin_dir}/qilin" 2>/dev/null || true
        fi
    fi

    info "installed to ${bin_dir}/qilin"

    case ":$PATH:" in
        *":${bin_dir}:"*) ;;
        *) warn "${bin_dir} is not in your \$PATH; add 'export PATH=\"${bin_dir}:\$PATH\"' to your shell rc" ;;
    esac

    info "run 'qilin init' to get started"
}

main "$@"
