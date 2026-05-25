package cli

import (
	"bytes"
	"runtime"
	"strings"
	"testing"

	"github.com/dev-creations/qilin/cli/internal/version"
)

// withVars temporarily overrides the version package's ldflag-injected vars
// so the assertions don't depend on whether the test binary was built with
// GoReleaser or plain `go test`.
func withVars(t *testing.T, v, commit, date string, f func()) {
	t.Helper()
	prevV, prevC, prevD := version.Version, version.Commit, version.Date
	version.Version, version.Commit, version.Date = v, commit, date
	t.Cleanup(func() {
		version.Version, version.Commit, version.Date = prevV, prevC, prevD
	})
	f()
}

func runRoot(t *testing.T, args ...string) string {
	t.Helper()
	root := NewRoot()
	var out bytes.Buffer
	root.SetOut(&out)
	root.SetErr(&out)
	root.SetArgs(args)
	if err := root.Execute(); err != nil {
		t.Fatalf("Execute(%v): %v", args, err)
	}
	return out.String()
}

func TestVersionSubcommandPrintsLong(t *testing.T) {
	withVars(t, "v4.5.6", "deadbeef00112233", "2025-03-04T05:06:07Z", func() {
		got := runRoot(t, "version")

		for _, want := range []string{
			"qilin version v4.5.6",
			"commit:",
			"deadbeef0011", // 12-char truncation
			"built:",
			"2025-03-04T05:06:07Z",
			"go:",
			runtime.Version(),
			"platform:",
			runtime.GOOS,
			runtime.GOARCH,
		} {
			if !strings.Contains(got, want) {
				t.Errorf("`qilin version` output missing %q\n---\n%s", want, got)
			}
		}
	})
}

func TestVersionFlagPrintsSameLongFormat(t *testing.T) {
	// `--version` uses the version template wired up in NewRoot(); it should
	// match the subcommand output.
	withVars(t, "v4.5.6", "deadbeef00112233", "2025-03-04T05:06:07Z", func() {
		flagOut := runRoot(t, "--version")
		subOut := runRoot(t, "version")

		if !strings.Contains(flagOut, "qilin version v4.5.6") {
			t.Errorf("--version output missing version line:\n%s", flagOut)
		}
		// Sanity: both surface the same multi-line payload.
		for _, line := range []string{"commit:", "built:", "go:", "platform:"} {
			if !strings.Contains(flagOut, line) {
				t.Errorf("--version output missing %q\n---\n%s", line, flagOut)
			}
			if !strings.Contains(subOut, line) {
				t.Errorf("version subcommand output missing %q\n---\n%s", line, subOut)
			}
		}
	})
}

func TestVersionSubcommandRejectsArgs(t *testing.T) {
	root := NewRoot()
	var out bytes.Buffer
	root.SetOut(&out)
	root.SetErr(&out)
	root.SetArgs([]string{"version", "extra"})
	if err := root.Execute(); err == nil {
		t.Fatalf("expected `qilin version extra` to fail with NoArgs validator, got nil")
	}
}
