# homebrew-qilin

Homebrew tap for [**qilin**](https://github.com/dev-creations/qilin) — a plug-and-play vector memory exposed over MCP/SSE.

This repository hosts the Homebrew **cask** that installs the `qilin` CLI on macOS (and Linuxbrew). The cask is generated and published automatically by [GoReleaser](https://goreleaser.com/) from the upstream [`dev-creations/qilin`](https://github.com/dev-creations/qilin) repository on every tagged release — please don't edit `Casks/qilin.rb` by hand; open issues and PRs against the upstream repo instead.

> Why a cask, not a formula? GoReleaser deprecated its `brews:` (formula) pipeline in v2.16 in favour of casks, which now work on Linuxbrew as well. See the [GoReleaser deprecation note](https://goreleaser.com/deprecations/#brews) for details.

## Install

```bash
brew tap dev-creations/qilin
brew install --cask qilin
```

Or in one shot, without an explicit `brew tap`:

```bash
brew install --cask dev-creations/qilin/qilin
```

This drops the `qilin` binary into Homebrew's prefix (`/opt/homebrew/bin/qilin` on Apple Silicon, `/usr/local/bin/qilin` on Intel). Run `qilin init` to bootstrap a config, then `qilin up` to start the MCP server. See the [upstream README](https://github.com/dev-creations/qilin#quick-start) for the full quick-start.

## Upgrade

```bash
brew update
brew upgrade --cask qilin
```

## Uninstall

```bash
brew uninstall --cask qilin
brew untap dev-creations/qilin   # optional, removes this tap
```

## Supported platforms

The cask ships prebuilt binaries for:

- macOS on Apple Silicon (`arm64`)
- macOS on Intel (`x86_64`)

For Linux, use the install script or build from source — see the [upstream install docs](https://github.com/dev-creations/qilin#install-the-cli). For Windows, use the [Scoop bucket](https://github.com/dev-creations/scoop-qilin) or the PowerShell installer.

## Verifying releases

Each upstream release publishes a `checksums.txt` alongside the archives. The cask pins the SHA-256 of the macOS archives, so `brew install` will fail loudly if a download is tampered with. You can also verify manually:

```bash
shasum -a 256 -c checksums.txt
```

## Reporting issues

- Bugs in the `qilin` CLI itself → [dev-creations/qilin/issues](https://github.com/dev-creations/qilin/issues)
- Problems specific to the Homebrew cask (install path, cask metadata, etc.) → file an issue here, but please confirm the upstream release archives work first.

## License

The cask metadata in this repository is released under the [MIT License](https://github.com/dev-creations/qilin/blob/main/LICENSE), matching upstream.
