#!/usr/bin/env sh
# Ad-hoc codesign a darwin binary using `rcodesign`.
#
# Invoked by goreleaser as a post-build hook (see ../.goreleaser.yaml):
#
#     hooks:
#       post:
#         - ./scripts/codesign-darwin.sh {{ .Os }} {{ .Path }}
#
# GoReleaser cross-compiles every target from a Linux runner. Cross-compiled
# darwin binaries leave the runner with *no* Mach-O code signature, which on
# Apple Silicon makes them unrunnable (the kernel kills any arm64 binary that
# lacks at least an ad-hoc signature) and on Intel triggers a Gatekeeper
# "Apple cannot verify..." dialog the first time they're launched.
#
# A real fix needs an Apple Developer ID + notarization. Ad-hoc signing here
# is the free middle ground: it gets the binary past the kernel check on
# arm64, and combined with stripping the `com.apple.quarantine` xattr in the
# Homebrew cask hook + scripts/install.sh, it gets us a clean
# `qilin --version` after install on every modern macOS.
#
# This script is a no-op for non-darwin builds so the same hook can fire for
# the entire build matrix without filtering.

set -eu

os="$1"
path="$2"

if [ "$os" != "darwin" ]; then
    exit 0
fi

if ! command -v rcodesign >/dev/null 2>&1; then
    printf 'codesign-darwin: rcodesign not found on PATH; skipping %s\n' "$path" >&2
    exit 0
fi

printf 'codesign-darwin: ad-hoc signing %s\n' "$path" >&2
exec rcodesign sign "$path"
