// Package compose renders the docker-compose file that `qilin up` runs against.
//
// We deliberately do not import a YAML library: the template is small, the
// output needs to be human-readable so users can `qilin config edit` it, and
// keeping the dependency surface thin matters for a binary that ships via
// curl-pipe installers.
package compose

import (
	"bytes"
	_ "embed"
	"fmt"
	"path/filepath"
	"text/template"

	"github.com/dev-creations/qilin/cli/internal/config"
)

//go:embed compose.tmpl.yaml
var composeTmpl string

// Input is the data passed to the compose template. Constructed from a
// Config + a paths.Layout by Render.
type Input struct {
	// MCP server bits
	Image       string
	BindHost    string
	TLSPort     int
	HTTPEnabled bool
	HTTPPort    int

	// Managed-mode Qdrant. Empty/zero when Managed is false.
	ManagedQdrant     bool
	QdrantImage       string
	QdrantHostPort    int

	// Host paths mounted into the qilin-mcp container.
	HostCertsDir      string
	HostQdrantDataDir string

	// Env file path (absolute) for docker compose to consume.
	EnvFile string
}

// Render returns the compose.yaml contents for c, with cert and data
// directories sourced from layout. certsDir is the directory mounted to
// /certs inside the container; dataDir holds the managed Qdrant volume bind
// (empty in external mode).
func Render(c *config.Config, certsDir, dataDir, envFile, defaultImage string) ([]byte, error) {
	if c == nil {
		return nil, fmt.Errorf("compose: nil config")
	}
	in := Input{
		Image:             pickImage(c.Image, defaultImage),
		BindHost:          c.Server.Host,
		TLSPort:           c.Server.Port,
		HTTPEnabled:       c.Server.HTTPEnabled,
		HTTPPort:          c.Server.HTTPPort,
		ManagedQdrant:     c.Qdrant.Managed,
		QdrantImage:       qdrantImage(c.Qdrant.ImageTag),
		QdrantHostPort:    c.Qdrant.HostPort,
		HostCertsDir:      filepath.ToSlash(certsDir),
		HostQdrantDataDir: filepath.ToSlash(dataDir),
		EnvFile:           filepath.ToSlash(envFile),
	}
	if in.QdrantHostPort == 0 {
		in.QdrantHostPort = config.DefaultManagedQdrantPort
	}

	tmpl, err := template.New("compose").Parse(composeTmpl)
	if err != nil {
		return nil, fmt.Errorf("parse compose template: %w", err)
	}
	var buf bytes.Buffer
	if err := tmpl.Execute(&buf, in); err != nil {
		return nil, fmt.Errorf("render compose template: %w", err)
	}
	return buf.Bytes(), nil
}

func pickImage(configured, fallback string) string {
	if configured != "" {
		return configured
	}
	if fallback != "" {
		return fallback
	}
	return "ghcr.io/dev-creations/qilin-mcp:latest"
}

func qdrantImage(tag string) string {
	if tag == "" {
		tag = config.DefaultQdrantImageTag
	}
	return "qdrant/qdrant:" + tag
}
