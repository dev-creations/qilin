package compose

import (
	"strings"
	"testing"

	"github.com/dev-creations/qilin/cli/internal/config"
)

func managedConfig() *config.Config {
	c := config.New()
	c.Image = "ghcr.io/dev-creations/qilin-mcp:v1.2.3"
	c.TLS.CertFile = "/some/host/certs/cert.pem"
	c.TLS.KeyFile = "/some/host/certs/key.pem"
	return &c
}

func externalConfig() *config.Config {
	c := managedConfig()
	c.Qdrant.Managed = false
	c.Qdrant.URL = "https://qdrant.example.com:6333"
	c.Qdrant.APIKey = "abc"
	return c
}

func TestRenderManagedIncludesBothServices(t *testing.T) {
	c := managedConfig()
	out, err := Render(c, "/home/user/.qilin/certs", "/home/user/.qilin/data", "/home/user/.qilin/.env", c.Image)
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	s := string(out)

	if !strings.Contains(s, "qilin-mcp:") {
		t.Errorf("missing qilin-mcp service")
	}
	if !strings.Contains(s, "qdrant:") {
		t.Errorf("managed mode should include qdrant service")
	}
	if !strings.Contains(s, "depends_on:") {
		t.Errorf("managed mode should add depends_on")
	}
	if !strings.Contains(s, "qdrant_data") {
		t.Errorf("managed mode should declare qdrant_data volume")
	}
	if !strings.Contains(s, c.Image) {
		t.Errorf("compose did not embed configured image %q", c.Image)
	}
}

func TestRenderExternalOmitsQdrant(t *testing.T) {
	c := externalConfig()
	out, err := Render(c, "/h/certs", "/h/data", "/h/.env", c.Image)
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	s := string(out)
	if strings.Contains(s, "qdrant:") {
		t.Errorf("external mode must not embed a qdrant service")
	}
	if strings.Contains(s, "qdrant_data") {
		t.Errorf("external mode must not declare qdrant_data volume")
	}
	if strings.Contains(s, "depends_on:") {
		t.Errorf("external mode must not depend_on")
	}
}

func TestRenderHTTPToggle(t *testing.T) {
	c := managedConfig()
	c.Server.HTTPEnabled = false
	out, _ := Render(c, "/c", "/d", "/e", "img")
	if strings.Contains(string(out), "127.0.0.1:8080") {
		t.Errorf("HTTP listener should be absent when disabled")
	}

	c.Server.HTTPEnabled = true
	out, _ = Render(c, "/c", "/d", "/e", "img")
	if !strings.Contains(string(out), "127.0.0.1:8080:8080") {
		t.Errorf("HTTP listener line missing; got:\n%s", out)
	}
}

func TestRenderBindHostPropagates(t *testing.T) {
	c := managedConfig()
	c.Server.Host = "0.0.0.0"
	out, _ := Render(c, "/c", "/d", "/e", "img")
	if !strings.Contains(string(out), "0.0.0.0:8443:8443") {
		t.Errorf("bind host did not propagate to compose ports; got:\n%s", out)
	}
}

func TestRenderFallsBackToDefaultImage(t *testing.T) {
	c := managedConfig()
	c.Image = ""
	out, _ := Render(c, "/c", "/d", "/e", "")
	if !strings.Contains(string(out), "ghcr.io/dev-creations/qilin-mcp:latest") {
		t.Errorf("expected fallback to :latest, got:\n%s", out)
	}
}
