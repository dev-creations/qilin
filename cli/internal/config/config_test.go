package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestNewDefaults(t *testing.T) {
	c := New()
	if c.SchemaVersion != SchemaVersion {
		t.Errorf("SchemaVersion: want %d, got %d", SchemaVersion, c.SchemaVersion)
	}
	if c.Collection != DefaultCollection {
		t.Errorf("Collection: want %s, got %s", DefaultCollection, c.Collection)
	}
	if !c.Qdrant.Managed {
		t.Errorf("New() should default to managed Qdrant")
	}
	if c.Server.Port != DefaultMCPPort {
		t.Errorf("Server.Port default mismatch")
	}
	if !c.Server.HTTPEnabled {
		t.Errorf("HTTPEnabled should default true")
	}
	if c.Ollama.EmbeddingDim != DefaultEmbeddingDim {
		t.Errorf("EmbeddingDim default mismatch")
	}
}

func validForTest() *Config {
	c := New()
	c.Image = "ghcr.io/dev-creations/qilin-mcp:latest"
	c.TLS.CertFile = "/certs/cert.pem"
	c.TLS.KeyFile = "/certs/key.pem"
	return &c
}

func TestValidateAcceptsValid(t *testing.T) {
	c := validForTest()
	if err := c.Validate(); err != nil {
		t.Fatalf("validForTest should pass: %v", err)
	}
}

func TestValidateRejectsConflicts(t *testing.T) {
	cases := []struct {
		name string
		mut  func(c *Config)
		want string
	}{
		{"empty collection", func(c *Config) { c.Collection = "" }, "default_collection"},
		{"bad port", func(c *Config) { c.Server.Port = 0 }, "server.port"},
		{"same ports", func(c *Config) { c.Server.HTTPPort = c.Server.Port }, "http_port must differ"},
		{"no ollama", func(c *Config) { c.Ollama.URL = "" }, "ollama.url"},
		{"external qdrant without url", func(c *Config) {
			c.Qdrant.Managed = false
			c.Qdrant.URL = ""
		}, "qdrant.url"},
		{"empty cert paths", func(c *Config) { c.TLS.CertFile = "" }, "tls.cert_file"},
		{"chunk too small", func(c *Config) { c.Chunking.SizeTokens = 8 }, "size_tokens"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			c := validForTest()
			tc.mut(c)
			err := c.Validate()
			if err == nil {
				t.Fatalf("expected error containing %q, got nil", tc.want)
			}
			if !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("expected error containing %q, got: %v", tc.want, err)
			}
		})
	}
}

func TestSaveLoadRoundTrip(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "config.json")

	c := validForTest()
	c.Qdrant.APIKey = "secret"
	c.Server.Port = 9443
	if err := Save(path, c); err != nil {
		t.Fatalf("Save: %v", err)
	}

	info, err := os.Stat(path)
	if err != nil {
		t.Fatalf("stat: %v", err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Errorf("config file perms = %o, want 0600", info.Mode().Perm())
	}

	loaded, err := Load(path)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if loaded.Qdrant.APIKey != "secret" {
		t.Errorf("api key did not round-trip")
	}
	if loaded.Server.Port != 9443 {
		t.Errorf("port did not round-trip")
	}
	if loaded.SchemaVersion != SchemaVersion {
		t.Errorf("schema version did not round-trip")
	}
}

func TestLoadRejectsFutureSchema(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "config.json")
	c := validForTest()
	c.SchemaVersion = SchemaVersion + 99
	if err := Save(path, c); err != nil {
		t.Fatalf("Save: %v", err)
	}
	if _, err := Load(path); err == nil {
		t.Fatalf("Load should reject schema version from the future")
	}
}

func TestEnvFileContainsAllKeys(t *testing.T) {
	c := validForTest()
	out := c.EnvFile("/certs/cert.pem", "/certs/key.pem")
	for _, k := range []string{
		"OLLAMA_BASE_URL", "EMBEDDING_MODEL", "EMBEDDING_DIM",
		"QDRANT_URL", "DEFAULT_COLLECTION",
		"CHUNK_SIZE_TOKENS", "CHUNK_OVERLAP_TOKENS", "EMBED_BATCH_SIZE",
		"MCP_HOST", "MCP_PORT", "MCP_HTTP_PORT", "MCP_HTTP_ENABLED",
		"TLS_CERT_FILE", "TLS_KEY_FILE",
	} {
		if !strings.Contains(out, k+"=") {
			t.Errorf(".env is missing %s", k)
		}
	}
}

func TestEnvManagedQdrantPointsAtServiceName(t *testing.T) {
	c := validForTest()
	c.Qdrant.Managed = true
	c.Qdrant.URL = ""
	env := c.EnvMap("/c/cert.pem", "/c/key.pem")
	if env["QDRANT_URL"] != "http://qdrant:6333" {
		t.Errorf("managed mode should point QDRANT_URL at the compose service, got %q", env["QDRANT_URL"])
	}
}

func TestSetDottedKeys(t *testing.T) {
	c := validForTest()
	if err := c.Set("server.port", "9999"); err != nil {
		t.Fatalf("Set server.port: %v", err)
	}
	if c.Server.Port != 9999 {
		t.Errorf("port not updated")
	}

	if err := c.Set("qdrant.url", "http://remote:6333"); err != nil {
		t.Fatalf("Set qdrant.url: %v", err)
	}
	if c.Qdrant.Managed {
		t.Errorf("setting a real qdrant.url should flip managed off")
	}

	if err := c.Set("schema_version", "99"); err == nil {
		t.Errorf("schema_version should be read-only")
	}
	if err := c.Set("nope", "x"); err == nil {
		t.Errorf("unknown keys should error")
	}
}
