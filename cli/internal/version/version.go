// Package version exposes build-time metadata for the qilin CLI.
//
// The values are overridden at link time via:
//
//	-ldflags "-X github.com/dev-creations/qilin/cli/internal/version.Version=v1.2.3 \
//	          -X github.com/dev-creations/qilin/cli/internal/version.Commit=abcdef \
//	          -X github.com/dev-creations/qilin/cli/internal/version.Date=2025-01-01T00:00:00Z"
//
// GoReleaser sets them automatically. `go build` from source leaves them at
// their dev defaults; in that case we fall back to runtime/debug.BuildInfo
// (`vcs.revision`, `vcs.time`, `vcs.modified`, and `Main.Version` for
// `go install ...@vX.Y.Z` style builds).
package version

import (
	"fmt"
	"runtime"
	"runtime/debug"
)

// These are overridden at link time by GoReleaser. Leave them as
// non-const var declarations so `go build -ldflags -X ...` can rewrite them.
var (
	Version = "dev"
	Commit  = ""
	Date    = ""
)

// resolved holds the effective version metadata after merging ldflag values
// with the runtime/debug.BuildInfo fallbacks.
type resolved struct {
	version string
	commit  string
	date    string
	dirty   bool
}

// resolve merges the ldflag-injected vars with what we can recover from
// runtime/debug.BuildInfo. Ldflags always win; the BuildInfo data only
// fills in fields that were left empty / at their dev default.
func resolve() resolved {
	r := resolved{
		version: Version,
		commit:  Commit,
		date:    Date,
	}

	info, ok := debug.ReadBuildInfo()
	if !ok {
		return r
	}

	if r.version == "" || r.version == "dev" {
		if v := info.Main.Version; v != "" && v != "(devel)" {
			r.version = v
		}
	}

	for _, setting := range info.Settings {
		switch setting.Key {
		case "vcs.revision":
			if r.commit == "" {
				r.commit = setting.Value
			}
		case "vcs.time":
			if r.date == "" {
				r.date = setting.Value
			}
		case "vcs.modified":
			if setting.Value == "true" {
				r.dirty = true
			}
		}
	}

	return r
}

// shortCommit truncates a commit hash to 12 chars, appending "-dirty" if the
// working tree was modified at build time.
func shortCommit(commit string, dirty bool) string {
	if commit == "" {
		commit = "unknown"
	}
	if len(commit) > 12 {
		commit = commit[:12]
	}
	if dirty {
		commit += "-dirty"
	}
	return commit
}

// ImageTag returns the Docker image tag the CLI should pull for the MCP
// server. Released builds pin to the matching semver tag; dev builds fall
// back to :latest so local development still works.
func ImageTag() string {
	v := resolve().version
	if v == "" || v == "dev" {
		return "latest"
	}
	return v
}

// String returns a single-line human-readable build identifier.
func String() string {
	r := resolve()
	return fmt.Sprintf("qilin %s (%s, %s/%s)",
		r.version, shortCommit(r.commit, r.dirty), runtime.GOOS, runtime.GOARCH)
}

// Long returns a multi-line build identifier suitable for `qilin version`.
func Long() string {
	r := resolve()
	date := r.date
	if date == "" {
		date = "unknown"
	}
	return fmt.Sprintf(
		"qilin version %s\n  commit:   %s\n  built:    %s\n  go:       %s\n  platform: %s/%s",
		r.version, shortCommit(r.commit, r.dirty), date, runtime.Version(), runtime.GOOS, runtime.GOARCH,
	)
}
