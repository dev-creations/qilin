package paths

import (
	"os"
	"path/filepath"
	"testing"
)

// abs normalises p the same way Resolve does (filepath.Abs + filepath.Clean),
// so test expectations match cross-platform: on Windows /foo gets the current
// drive prefixed, on POSIX it stays as /foo.
func abs(t *testing.T, p string) string {
	t.Helper()
	a, err := filepath.Abs(p)
	if err != nil {
		t.Fatalf("filepath.Abs(%q): %v", p, err)
	}
	return filepath.Clean(a)
}

func TestResolveExplicitWins(t *testing.T) {
	t.Setenv("QILIN_HOME", filepath.Join(t.TempDir(), "from-env"))
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(t.TempDir(), "from-xdg"))

	explicit := filepath.Join(t.TempDir(), "from-flag")
	got, err := Resolve(explicit)
	if err != nil {
		t.Fatalf("Resolve: %v", err)
	}
	if got.Home != abs(t, explicit) {
		t.Fatalf("explicit flag should win, got %s, want %s", got.Home, abs(t, explicit))
	}
}

func TestResolveEnvOverridesXDG(t *testing.T) {
	envHome := filepath.Join(t.TempDir(), "from-env")
	t.Setenv("QILIN_HOME", envHome)
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(t.TempDir(), "from-xdg"))

	got, err := Resolve("")
	if err != nil {
		t.Fatalf("Resolve: %v", err)
	}
	if got.Home != abs(t, envHome) {
		t.Fatalf("QILIN_HOME should win over XDG, got %s, want %s", got.Home, abs(t, envHome))
	}
}

func TestResolveXDGFallback(t *testing.T) {
	xdg := filepath.Join(t.TempDir(), "xdg")
	t.Setenv("QILIN_HOME", "")
	t.Setenv("XDG_CONFIG_HOME", xdg)

	got, err := Resolve("")
	if err != nil {
		t.Fatalf("Resolve: %v", err)
	}
	want := abs(t, filepath.Join(xdg, "qilin"))
	if got.Home != want {
		t.Fatalf("XDG fallback: want %s, got %s", want, got.Home)
	}
}

func TestResolvePopulatesAllPaths(t *testing.T) {
	t.Setenv("QILIN_HOME", "")
	t.Setenv("XDG_CONFIG_HOME", "")

	base := filepath.Join(t.TempDir(), "qilintest")
	got, err := Resolve(base)
	if err != nil {
		t.Fatalf("Resolve: %v", err)
	}
	absBase := abs(t, base)
	checks := []struct {
		name, got, want string
	}{
		{"Config", got.Config, filepath.Join(absBase, ConfigFileName)},
		{"Env", got.Env, filepath.Join(absBase, EnvFileName)},
		{"Compose", got.Compose, filepath.Join(absBase, ComposeFileName)},
		{"Certs", got.Certs, filepath.Join(absBase, CertsDirName)},
		{"Cert", got.Cert, filepath.Join(absBase, CertsDirName, CertFileName)},
		{"Key", got.Key, filepath.Join(absBase, CertsDirName, KeyFileName)},
	}
	for _, c := range checks {
		if c.got != c.want {
			t.Errorf("%s: want %s, got %s", c.name, c.want, c.got)
		}
	}
}

func TestEnsureHomeCreatesTreeIdempotently(t *testing.T) {
	dir := t.TempDir()
	l, err := Resolve(dir)
	if err != nil {
		t.Fatalf("Resolve: %v", err)
	}
	for i := 0; i < 2; i++ {
		if err := l.EnsureHome(); err != nil {
			t.Fatalf("EnsureHome iter %d: %v", i, err)
		}
	}
	for _, d := range []string{l.Home, l.Certs, l.Data} {
		if !isDir(t, d) {
			t.Errorf("EnsureHome did not create %s", d)
		}
	}
	if l.ConfigExists() {
		t.Errorf("ConfigExists should be false before init writes config.json")
	}
}

func isDir(t *testing.T, p string) bool {
	t.Helper()
	info, err := os.Stat(p)
	if err != nil {
		return false
	}
	return info.IsDir()
}
