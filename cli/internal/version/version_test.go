package version

import (
	"runtime"
	"strings"
	"testing"
)

// withVars temporarily overrides the package-level ldflag vars for the
// duration of f, then restores them. This simulates what `-ldflags -X ...`
// would do at link time.
func withVars(t *testing.T, version, commit, date string, f func()) {
	t.Helper()
	prevV, prevC, prevD := Version, Commit, Date
	Version, Commit, Date = version, commit, date
	t.Cleanup(func() {
		Version, Commit, Date = prevV, prevC, prevD
	})
	f()
}

func TestImageTagDevFallsBackToLatest(t *testing.T) {
	withVars(t, "dev", "", "", func() {
		if got := ImageTag(); got != "latest" {
			t.Errorf("ImageTag() for dev version = %q, want %q", got, "latest")
		}
	})
}

func TestImageTagUsesLdflagVersion(t *testing.T) {
	withVars(t, "v1.2.3", "abc123", "2025-01-01T00:00:00Z", func() {
		if got := ImageTag(); got != "v1.2.3" {
			t.Errorf("ImageTag() = %q, want %q", got, "v1.2.3")
		}
	})
}

func TestStringIncludesShortCommitAndPlatform(t *testing.T) {
	withVars(t, "v1.2.3", "abcdef0123456789deadbeef", "2025-01-01T00:00:00Z", func() {
		got := String()
		if !strings.Contains(got, "v1.2.3") {
			t.Errorf("String() = %q, missing version", got)
		}
		// 12-char truncation.
		if !strings.Contains(got, "abcdef012345") {
			t.Errorf("String() = %q, missing 12-char short commit", got)
		}
		if strings.Contains(got, "abcdef0123456789") {
			t.Errorf("String() = %q, commit not truncated", got)
		}
		if !strings.Contains(got, runtime.GOOS) || !strings.Contains(got, runtime.GOARCH) {
			t.Errorf("String() = %q, missing platform (%s/%s)", got, runtime.GOOS, runtime.GOARCH)
		}
	})
}

func TestLongContainsAllFields(t *testing.T) {
	withVars(t, "v9.9.9", "feedfacecafebabe", "2025-06-15T12:34:56Z", func() {
		got := Long()
		for _, want := range []string{
			"qilin version v9.9.9",
			"commit:",
			"feedfacecafe", // 12-char truncation
			"built:",
			"2025-06-15T12:34:56Z",
			"go:",
			runtime.Version(),
			"platform:",
			runtime.GOOS,
			runtime.GOARCH,
		} {
			if !strings.Contains(got, want) {
				t.Errorf("Long() = %q, missing %q", got, want)
			}
		}
	})
}

func TestLongFillsUnknownsForDevBuild(t *testing.T) {
	// Tests run via `go test` produce a BuildInfo with no vcs.* settings,
	// so the date should resolve to "unknown" and the commit to either
	// "unknown" or whatever debug.ReadBuildInfo can recover. We only assert
	// the "built: unknown" path because vcs.time is unset in `go test` builds.
	withVars(t, "dev", "", "", func() {
		got := Long()
		if !strings.Contains(got, "built:    unknown") {
			t.Errorf("Long() = %q, expected `built:    unknown` for dev build", got)
		}
	})
}

func TestShortCommitDirtySuffix(t *testing.T) {
	if got := shortCommit("abcdef0123456789", true); got != "abcdef012345-dirty" {
		t.Errorf("shortCommit dirty = %q, want %q", got, "abcdef012345-dirty")
	}
	if got := shortCommit("abcdef0123456789", false); got != "abcdef012345" {
		t.Errorf("shortCommit clean = %q, want %q", got, "abcdef012345")
	}
	if got := shortCommit("", false); got != "unknown" {
		t.Errorf("shortCommit empty = %q, want %q", got, "unknown")
	}
	if got := shortCommit("", true); got != "unknown-dirty" {
		t.Errorf("shortCommit empty dirty = %q, want %q", got, "unknown-dirty")
	}
	if got := shortCommit("abc", false); got != "abc" {
		t.Errorf("shortCommit short = %q, want %q", got, "abc")
	}
}
