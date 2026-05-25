// Package version exposes build-time metadata for the qilin CLI.
//
// The values are overridden at link time via:
//
//	-ldflags "-X github.com/dev-creations/qilin/cli/internal/version.Version=v1.2.3 \
//	          -X github.com/dev-creations/qilin/cli/internal/version.Commit=abcdef \
//	          -X github.com/dev-creations/qilin/cli/internal/version.Date=2025-01-01T00:00:00Z"
//
// GoReleaser sets them automatically; `go build` from source leaves them at
// their dev defaults.
package version

import (
	"fmt"
	"runtime"
	"runtime/debug"
)

var (
	Version = "dev"
	Commit  = ""
	Date    = ""
)

// ImageTag returns the Docker image tag the CLI should pull for the MCP
// server. Released builds pin to the matching semver tag; dev builds fall
// back to :latest so local development still works.
func ImageTag() string {
	if Version == "" || Version == "dev" {
		return "latest"
	}
	return Version
}

// String returns a single-line human-readable build identifier.
func String() string {
	commit := Commit
	if commit == "" {
		commit = readVCSRevision()
	}
	if commit == "" {
		commit = "unknown"
	}
	short := commit
	if len(short) > 12 {
		short = short[:12]
	}
	return fmt.Sprintf("qilin %s (%s, %s/%s)", Version, short, runtime.GOOS, runtime.GOARCH)
}

// Long returns a multi-line build identifier suitable for `qilin version`.
func Long() string {
	commit := Commit
	if commit == "" {
		commit = readVCSRevision()
	}
	if commit == "" {
		commit = "unknown"
	}
	date := Date
	if date == "" {
		date = "unknown"
	}
	return fmt.Sprintf(
		"qilin version %s\n  commit:   %s\n  built:    %s\n  go:       %s\n  platform: %s/%s",
		Version, commit, date, runtime.Version(), runtime.GOOS, runtime.GOARCH,
	)
}

func readVCSRevision() string {
	info, ok := debug.ReadBuildInfo()
	if !ok {
		return ""
	}
	for _, setting := range info.Settings {
		if setting.Key == "vcs.revision" {
			return setting.Value
		}
	}
	return ""
}
