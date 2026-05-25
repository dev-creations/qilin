// Package wizard renders the interactive `qilin init` form.
//
// It produces an Answers struct that the init command then folds into a
// config.Config. Headless / CI callers should not invoke Run -- they should
// build the same Answers from flags directly.
package wizard

import (
	"fmt"

	"github.com/charmbracelet/huh"

	"github.com/dev-creations/qilin/cli/internal/config"
)

// Answers is the typed result of a successful wizard run. Empty strings on
// optional fields mean "user accepted the default" and should be resolved
// against config.New() values.
type Answers struct {
	QdrantMode     string // "managed" | "external"
	QdrantURL      string
	QdrantAPIKey   string
	OllamaURL      string
	EmbeddingModel string
	ServerHost     string
	ServerPort     int
	HTTPEnabled    bool
	HTTPPort       int
	Collection     string
	UseExistingTLS bool
	TLSCertFile    string
	TLSKeyFile     string
}

// Defaults seeds the wizard's answer struct with library defaults. Each
// individual prompt then overrides a field via huh's pointer binding.
func Defaults() Answers {
	return Answers{
		QdrantMode:     "managed",
		OllamaURL:      config.DefaultOllamaURL,
		EmbeddingModel: config.DefaultEmbeddingModel,
		ServerHost:     config.DefaultMCPHost,
		ServerPort:     config.DefaultMCPPort,
		HTTPEnabled:    true,
		HTTPPort:       config.DefaultMCPHTTPPort,
		Collection:     config.DefaultCollection,
		UseExistingTLS: false,
	}
}

// Run presents the wizard and returns the user's choices. If the user
// cancels, it returns huh.ErrUserAborted.
func Run() (Answers, error) {
	a := Defaults()

	portStr := fmt.Sprintf("%d", a.ServerPort)
	httpPortStr := fmt.Sprintf("%d", a.HTTPPort)

	form := huh.NewForm(
		huh.NewGroup(
			huh.NewSelect[string]().
				Title("Qdrant").
				Description("Where should Qilin keep its vectors?").
				Options(
					huh.NewOption("Managed (start a local container)", "managed"),
					huh.NewOption("External (point at an existing Qdrant)", "external"),
				).
				Value(&a.QdrantMode),
		),

		huh.NewGroup(
			huh.NewInput().
				Title("Qdrant URL").
				Description("e.g. https://qdrant.example.com:6333").
				Validate(requireURLIfExternal(&a.QdrantMode)).
				Value(&a.QdrantURL),
			huh.NewInput().
				Title("Qdrant API key").
				Description("Optional. Leave blank if your Qdrant has no auth.").
				EchoMode(huh.EchoModePassword).
				Value(&a.QdrantAPIKey),
		).WithHideFunc(func() bool { return a.QdrantMode != "external" }),

		huh.NewGroup(
			huh.NewInput().
				Title("Ollama URL").
				Description("Where to reach the embedding model.").
				Validate(nonEmpty("Ollama URL")).
				Value(&a.OllamaURL),
			huh.NewInput().
				Title("Embedding model").
				Validate(nonEmpty("embedding model")).
				Value(&a.EmbeddingModel),
		),

		huh.NewGroup(
			huh.NewInput().
				Title("MCP bind host").
				Description("Host interface the qilin-mcp container exposes ports on (127.0.0.1 keeps it local).").
				Value(&a.ServerHost),
			huh.NewInput().
				Title("MCP TLS port").
				Validate(validatePort).
				Value(&portStr),
			huh.NewConfirm().
				Title("Also expose plain HTTP (loopback only)?").
				Description("Useful for Cursor on Windows, which doesn't read NODE_EXTRA_CA_CERTS.").
				Value(&a.HTTPEnabled),
			huh.NewInput().
				Title("Plain HTTP port").
				Validate(validatePort).
				Value(&httpPortStr),
		),

		huh.NewGroup(
			huh.NewInput().
				Title("Default collection name").
				Validate(nonEmpty("collection name")).
				Value(&a.Collection),
		),

		huh.NewGroup(
			huh.NewConfirm().
				Title("Bring your own TLS certificate?").
				Description("Default: qilin generates a self-signed cert in ${QILIN_HOME}/certs/.").
				Value(&a.UseExistingTLS),
		),

		huh.NewGroup(
			huh.NewInput().
				Title("Path to TLS cert (PEM)").
				Validate(nonEmpty("cert path")).
				Value(&a.TLSCertFile),
			huh.NewInput().
				Title("Path to TLS key (PEM)").
				Validate(nonEmpty("key path")).
				Value(&a.TLSKeyFile),
		).WithHideFunc(func() bool { return !a.UseExistingTLS }),
	)

	if err := form.Run(); err != nil {
		return Answers{}, err
	}

	if _, err := parsePort(portStr); err == nil {
		a.ServerPort, _ = parsePort(portStr)
	}
	if _, err := parsePort(httpPortStr); err == nil {
		a.HTTPPort, _ = parsePort(httpPortStr)
	}

	return a, nil
}
