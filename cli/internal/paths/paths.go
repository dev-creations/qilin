// Package paths resolves filesystem locations used by the qilin CLI.
//
// All on-disk state lives under QILIN_HOME, defaulting to ~/.qilin. Tests and
// CI override the location via $QILIN_HOME or the --qilin-home flag; the
// resolver gives them a single, layered source of truth.
package paths

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

// File and directory names inside QILIN_HOME. Keeping them as constants lets
// callers compose paths without sprinkling magic strings.
const (
	ConfigFileName  = "config.json"
	EnvFileName     = ".env"
	ComposeFileName = "compose.yaml"
	CertsDirName    = "certs"
	CertFileName    = "cert.pem"
	KeyFileName     = "key.pem"
	DataDirName     = "data"
)

// Layout holds every well-known path the CLI cares about for a single
// QILIN_HOME. Construct one with Resolve and then read fields off of it
// instead of re-deriving paths in command code.
type Layout struct {
	Home    string
	Config  string
	Env     string
	Compose string
	Certs   string
	Cert    string
	Key     string
	Data    string
}

// Resolve picks the QILIN_HOME directory, falling back through the following
// precedence chain (highest first):
//
//  1. explicit (the --qilin-home flag, if non-empty)
//  2. the $QILIN_HOME environment variable, if non-empty
//  3. $XDG_CONFIG_HOME/qilin, if $XDG_CONFIG_HOME is set
//  4. $HOME/.qilin
//
// The returned path is absolute and cleaned, but is *not* created on disk;
// callers that need the directory to exist should call EnsureHome on the
// resulting Layout.
func Resolve(explicit string) (Layout, error) {
	home, err := resolveHome(explicit)
	if err != nil {
		return Layout{}, err
	}
	abs, err := filepath.Abs(home)
	if err != nil {
		return Layout{}, fmt.Errorf("resolve qilin home: %w", err)
	}
	abs = filepath.Clean(abs)
	return Layout{
		Home:    abs,
		Config:  filepath.Join(abs, ConfigFileName),
		Env:     filepath.Join(abs, EnvFileName),
		Compose: filepath.Join(abs, ComposeFileName),
		Certs:   filepath.Join(abs, CertsDirName),
		Cert:    filepath.Join(abs, CertsDirName, CertFileName),
		Key:     filepath.Join(abs, CertsDirName, KeyFileName),
		Data:    filepath.Join(abs, DataDirName),
	}, nil
}

func resolveHome(explicit string) (string, error) {
	if explicit != "" {
		return explicit, nil
	}
	if env := os.Getenv("QILIN_HOME"); env != "" {
		return env, nil
	}
	if xdg := os.Getenv("XDG_CONFIG_HOME"); xdg != "" {
		return filepath.Join(xdg, "qilin"), nil
	}
	usr, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf("locate user home directory: %w", err)
	}
	if usr == "" {
		return "", errors.New("user home directory is empty; set $HOME or $QILIN_HOME")
	}
	return filepath.Join(usr, ".qilin"), nil
}

// EnsureHome creates the QILIN_HOME directory tree (Home, Certs, Data) with
// the right perms. It is idempotent.
func (l Layout) EnsureHome() error {
	for _, dir := range []string{l.Home, l.Certs, l.Data} {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return fmt.Errorf("create %s: %w", dir, err)
		}
	}
	return nil
}

// ConfigExists reports whether a config file has already been written.
func (l Layout) ConfigExists() bool {
	_, err := os.Stat(l.Config)
	return err == nil
}
