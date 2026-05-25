package paths

import (
	"os"
	"path/filepath"
	"testing"
)

func TestResolveExplicitWins(t *testing.T) {
	t.Setenv("QILIN_HOME", "/from-env")
	t.Setenv("XDG_CONFIG_HOME", "/from-xdg")

	got, err := Resolve("/from-flag")
	if err != nil {
		t.Fatalf("Resolve: %v", err)
	}
	if filepath.ToSlash(got.Home) != "/from-flag" {
		t.Fatalf("explicit flag should win, got %s", got.Home)
	}
}

func TestResolveEnvOverridesXDG(t *testing.T) {
	t.Setenv("QILIN_HOME", "/from-env")
	t.Setenv("XDG_CONFIG_HOME", "/from-xdg")

	got, err := Resolve("")
	if err != nil {
		t.Fatalf("Resolve: %v", err)
	}
	if filepath.ToSlash(got.Home) != "/from-env" {
		t.Fatalf("QILIN_HOME should win over XDG, got %s", got.Home)
	}
}

func TestResolveXDGFallback(t *testing.T) {
	t.Setenv("QILIN_HOME", "")
	t.Setenv("XDG_CONFIG_HOME", "/xdg")

	got, err := Resolve("")
	if err != nil {
		t.Fatalf("Resolve: %v", err)
	}
	want := filepath.Clean("/xdg/qilin")
	if got.Home != want {
		t.Fatalf("XDG fallback: want %s, got %s", want, got.Home)
	}
}

func TestResolvePopulatesAllPaths(t *testing.T) {
	t.Setenv("QILIN_HOME", "")
	t.Setenv("XDG_CONFIG_HOME", "")

	got, err := Resolve("/tmp/qilintest")
	if err != nil {
		t.Fatalf("Resolve: %v", err)
	}
	checks := []struct {
		name, got, want string
	}{
		{"Config", got.Config, filepath.Join("/tmp/qilintest", ConfigFileName)},
		{"Env", got.Env, filepath.Join("/tmp/qilintest", EnvFileName)},
		{"Compose", got.Compose, filepath.Join("/tmp/qilintest", ComposeFileName)},
		{"Certs", got.Certs, filepath.Join("/tmp/qilintest", CertsDirName)},
		{"Cert", got.Cert, filepath.Join("/tmp/qilintest", CertsDirName, CertFileName)},
		{"Key", got.Key, filepath.Join("/tmp/qilintest", CertsDirName, KeyFileName)},
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
