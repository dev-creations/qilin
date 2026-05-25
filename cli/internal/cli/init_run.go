package cli

import (
	"errors"
	"fmt"
	"io"
	"os"

	"github.com/spf13/cobra"
	"golang.org/x/term"

	"github.com/dev-creations/qilin/cli/internal/compose"
	"github.com/dev-creations/qilin/cli/internal/config"
	"github.com/dev-creations/qilin/cli/internal/paths"
	qtls "github.com/dev-creations/qilin/cli/internal/tls"
	"github.com/dev-creations/qilin/cli/internal/version"
	"github.com/dev-creations/qilin/cli/internal/wizard"
)

// Container-side cert paths. The qilin-mcp container expects TLS material
// under /certs/cert.pem and /certs/key.pem; we always mount $QILIN_HOME/certs
// onto /certs (read-only) so these paths are constant inside the container
// regardless of where the user's PEM files live on the host.
const (
	containerCertPath = "/certs/cert.pem"
	containerKeyPath  = "/certs/key.pem"
)

func runInit(cmd *cobra.Command, g *Globals, f *initFlags) error {
	out := cmd.OutOrStdout()

	layout, err := paths.Resolve(g.QilinHome)
	if err != nil {
		return err
	}

	if layout.ConfigExists() && !f.force {
		return fmt.Errorf(
			"config already exists at %s — re-run with --force to overwrite, "+
				"or use `qilin config edit` to tweak it",
			layout.Config,
		)
	}

	if err := layout.EnsureHome(); err != nil {
		return err
	}

	cfg := config.New()
	cfg.Image = pickImage(f.image)

	if err := applyAnswers(cmd, &cfg, &layout, f); err != nil {
		return err
	}

	if err := cfg.Validate(); err != nil {
		return fmt.Errorf("config: %w", err)
	}

	if cfg.TLS.SelfSigned {
		if err := generateCert(out, layout); err != nil {
			return err
		}
	} else {
		fmt.Fprintf(out, "Using user-provided TLS material:\n  cert: %s\n  key:  %s\n", cfg.TLS.CertFile, cfg.TLS.KeyFile)
	}

	if err := writeArtifacts(&cfg, layout); err != nil {
		return err
	}

	printBanner(out, &cfg, layout)
	return nil
}

func pickImage(flag string) string {
	if flag != "" {
		return flag
	}
	return "ghcr.io/dev-creations/qilin-mcp:" + version.ImageTag()
}

// applyAnswers folds the user's input (flags + maybe wizard) into cfg.
func applyAnswers(cmd *cobra.Command, cfg *config.Config, layout *paths.Layout, f *initFlags) error {
	answers, err := collectAnswers(cmd, f)
	if err != nil {
		return err
	}

	switch answers.QdrantMode {
	case "managed":
		cfg.Qdrant.Managed = true
		cfg.Qdrant.URL = ""
		cfg.Qdrant.APIKey = ""
	case "external":
		cfg.Qdrant.Managed = false
		cfg.Qdrant.URL = answers.QdrantURL
		cfg.Qdrant.APIKey = answers.QdrantAPIKey
	default:
		return fmt.Errorf("internal: unknown qdrant mode %q", answers.QdrantMode)
	}

	cfg.Ollama.URL = answers.OllamaURL
	cfg.Ollama.EmbeddingModel = answers.EmbeddingModel
	if f.embeddingDim > 0 {
		cfg.Ollama.EmbeddingDim = f.embeddingDim
	}

	cfg.Server.Host = answers.ServerHost
	cfg.Server.Port = answers.ServerPort
	cfg.Server.HTTPEnabled = answers.HTTPEnabled
	cfg.Server.HTTPPort = answers.HTTPPort

	cfg.Collection = answers.Collection

	if answers.UseExistingTLS {
		if answers.TLSCertFile == "" || answers.TLSKeyFile == "" {
			return errors.New("both --cert and --key (or wizard equivalents) must be provided")
		}
		cfg.TLS.CertFile = answers.TLSCertFile
		cfg.TLS.KeyFile = answers.TLSKeyFile
		cfg.TLS.SelfSigned = false
	} else {
		cfg.TLS.CertFile = layout.Cert
		cfg.TLS.KeyFile = layout.Key
		cfg.TLS.SelfSigned = true
	}

	return nil
}

// collectAnswers returns either the wizard's result (interactive case) or a
// flag-derived Answers struct (non-interactive case). It decides between the
// two based on flags, stdin TTY-ness, and any user-provided overrides.
func collectAnswers(cmd *cobra.Command, f *initFlags) (wizard.Answers, error) {
	if f.managedQdrant && f.externalQdrant {
		return wizard.Answers{}, errors.New("--managed-qdrant and --external-qdrant are mutually exclusive")
	}

	if shouldRunWizard(cmd, f) {
		a, err := wizard.Run()
		if err != nil {
			return wizard.Answers{}, err
		}
		return a, nil
	}

	return answersFromFlags(f)
}

// shouldRunWizard answers true when the user is interactive AND hasn't
// passed --non-interactive. We also auto-flip to non-interactive when stdin
// is not a TTY (CI, docker exec, etc.), so the same command works headless.
func shouldRunWizard(cmd *cobra.Command, f *initFlags) bool {
	if f.nonInteractive {
		return false
	}
	in, ok := cmd.InOrStdin().(*os.File)
	if !ok {
		return false
	}
	return term.IsTerminal(int(in.Fd()))
}

func answersFromFlags(f *initFlags) (wizard.Answers, error) {
	a := wizard.Defaults()

	mode := a.QdrantMode
	switch {
	case f.externalQdrant:
		mode = "external"
	case f.managedQdrant:
		mode = "managed"
	case f.qdrantURL != "":
		// providing a URL without an explicit mode implies external
		mode = "external"
	}
	a.QdrantMode = mode
	if mode == "external" {
		if f.qdrantURL == "" {
			return wizard.Answers{}, errors.New("external mode requires --qdrant-url")
		}
		a.QdrantURL = f.qdrantURL
		a.QdrantAPIKey = f.qdrantAPIKey
	}

	if f.ollamaURL != "" {
		a.OllamaURL = f.ollamaURL
	}
	if f.embeddingModel != "" {
		a.EmbeddingModel = f.embeddingModel
	}

	if f.host != "" {
		a.ServerHost = f.host
	}
	if f.port != 0 {
		a.ServerPort = f.port
	}
	if f.httpPort != 0 {
		a.HTTPPort = f.httpPort
	}
	if f.noHTTP {
		a.HTTPEnabled = false
	}

	if f.collection != "" {
		a.Collection = f.collection
	}

	switch {
	case f.certFile != "" && f.keyFile != "":
		a.UseExistingTLS = true
		a.TLSCertFile = f.certFile
		a.TLSKeyFile = f.keyFile
	case f.certFile != "" || f.keyFile != "":
		return wizard.Answers{}, errors.New("--cert and --key must be provided together")
	}

	return a, nil
}

func generateCert(out io.Writer, layout paths.Layout) error {
	fmt.Fprintf(out, "Generating self-signed TLS certificate...\n")
	mat, err := qtls.GenerateSelfSigned(qtls.Options{})
	if err != nil {
		return fmt.Errorf("generate cert: %w", err)
	}
	if err := mat.WritePair(layout.Cert, layout.Key); err != nil {
		return fmt.Errorf("write cert: %w", err)
	}
	fmt.Fprintf(out, "  wrote %s\n  wrote %s\n", layout.Cert, layout.Key)
	return nil
}

func writeArtifacts(cfg *config.Config, layout paths.Layout) error {
	if err := config.Save(layout.Config, cfg); err != nil {
		return err
	}
	envBody := cfg.EnvFile(containerCertPath, containerKeyPath)
	if err := os.WriteFile(layout.Env, []byte(envBody), 0o600); err != nil {
		return fmt.Errorf("write .env: %w", err)
	}
	composeBody, err := compose.Render(cfg, layout.Certs, layout.Data, layout.Env, cfg.Image)
	if err != nil {
		return err
	}
	if err := os.WriteFile(layout.Compose, composeBody, 0o644); err != nil {
		return fmt.Errorf("write compose.yaml: %w", err)
	}
	return nil
}

func printBanner(out io.Writer, cfg *config.Config, layout paths.Layout) {
	sseURL := fmt.Sprintf("https://localhost:%d/sse", cfg.Server.Port)
	plainURL := ""
	if cfg.Server.HTTPEnabled {
		plainURL = fmt.Sprintf("http://localhost:%d/sse", cfg.Server.HTTPPort)
	}

	fmt.Fprintln(out)
	fmt.Fprintln(out, "qilin is configured.")
	fmt.Fprintln(out)
	fmt.Fprintf(out, "  config:   %s\n", layout.Config)
	fmt.Fprintf(out, "  compose:  %s\n", layout.Compose)
	fmt.Fprintf(out, "  cert:     %s\n", layout.Cert)
	fmt.Fprintln(out)
	fmt.Fprintln(out, "Next steps:")
	fmt.Fprintln(out, "  1. Make sure Ollama is running with the embedding model pulled:")
	fmt.Fprintf(out, "       ollama pull %s\n", cfg.Ollama.EmbeddingModel)
	fmt.Fprintln(out, "  2. Start the stack:")
	fmt.Fprintln(out, "       qilin up")
	fmt.Fprintln(out, "  3. Add this snippet to your MCP client (e.g. ~/.cursor/mcp.json):")
	fmt.Fprintln(out)
	url := sseURL
	if plainURL != "" {
		url = plainURL
	}
	fmt.Fprintln(out, "       {")
	fmt.Fprintln(out, "         \"mcpServers\": {")
	fmt.Fprintln(out, "           \"qilin\": {")
	fmt.Fprintf(out, "             \"url\": %q\n", url)
	fmt.Fprintln(out, "           }")
	fmt.Fprintln(out, "         }")
	fmt.Fprintln(out, "       }")
	fmt.Fprintln(out)
	if plainURL != "" {
		fmt.Fprintln(out, "  TLS endpoint:  "+sseURL+"  (requires importing the self-signed cert)")
		fmt.Fprintln(out, "  Plain HTTP:    "+plainURL+"  (loopback only)")
	} else {
		fmt.Fprintln(out, "  TLS endpoint:  "+sseURL)
	}
}
