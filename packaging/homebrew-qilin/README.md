# homebrew-qilin

Homebrew tap for [**qilin**](https://github.com/dev-creations/qilin) — a plug-and-play vector memory exposed over MCP/SSE.

This repository hosts the Homebrew **formula** that installs the `qilin` CLI on macOS and Linux. The formula is generated and published automatically by [GoReleaser](https://goreleaser.com/) from the upstream [`dev-creations/qilin`](https://github.com/dev-creations/qilin) repository on every tagged release — please don't edit `Formula/qilin.rb` by hand; open issues and PRs against the upstream repo instead.

## Install

```bash
brew tap dev-creations/qilin
brew install qilin
```

Or in one shot, without an explicit `brew tap`:

```bash
brew install dev-creations/qilin/qilin
```

This drops the `qilin` binary into Homebrew's prefix (`/opt/homebrew/bin/qilin` on Apple Silicon, `/usr/local/bin/qilin` on Intel, `/home/linuxbrew/.linuxbrew/bin/qilin` on Linuxbrew). Run `qilin init` to bootstrap a config, then `qilin up` to start the MCP server. See the [upstream README](https://github.com/dev-creations/qilin#quick-start) for the full quick-start.

## Upgrade

```bash
brew update
brew upgrade qilin
```

## Uninstall

```bash
brew uninstall qilin
brew untap dev-creations/qilin   # optional, removes this tap
```

## Supported platforms

The formula ships prebuilt binaries for:

- macOS on Apple Silicon (`arm64`)
- macOS on Intel (`x86_64`)
- Linux on `x86_64`
- Linux on `arm64`

For Windows, use the [Scoop bucket](https://github.com/dev-creations/scoop-qilin) or the PowerShell installer.

## Verifying releases

Each upstream release publishes a `checksums.txt` alongside the archives. The formula pins the SHA-256 of each platform's archive, so `brew install` will fail loudly if a download is tampered with. You can also verify manually:

```bash
shasum -a 256 -c checksums.txt
```

## Reporting issues

- Bugs in the `qilin` CLI itself → [dev-creations/qilin/issues](https://github.com/dev-creations/qilin/issues)
- Problems specific to the Homebrew formula (install path, formula metadata, etc.) → file an issue here, but please confirm the upstream release archives work first.

## License

The formula in this repository is released under the [MIT License](https://github.com/dev-creations/qilin/blob/main/LICENSE), matching upstream.
