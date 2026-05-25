package cli

import (
	"github.com/spf13/cobra"
)

// initFlags captures the values passed to `qilin init`. Every interactive
// wizard field has a matching flag so CI users can run the command headless.
type initFlags struct {
	qdrantURL      string
	qdrantAPIKey   string
	managedQdrant  bool
	externalQdrant bool

	ollamaURL      string
	embeddingModel string
	embeddingDim   int

	host        string
	port        int
	httpPort    int
	noHTTP      bool

	certFile string
	keyFile  string

	collection string

	nonInteractive bool
	force          bool
	image          string
}

func newInitCmd(g *Globals) *cobra.Command {
	f := &initFlags{}

	cmd := &cobra.Command{
		Use:   "init",
		Short: "Generate qilin config, certs, and compose.yaml.",
		Long: "Set up everything qilin needs to run on this host: writes\n" +
			"${QILIN_HOME}/config.json, generates a self-signed TLS cert (unless\n" +
			"one is provided), and emits a docker compose project ready to go.\n\n" +
			"Run with no flags for an interactive wizard; pass --non-interactive\n" +
			"in CI together with whichever flags you need to override defaults.",
		Args: cobra.NoArgs,
	}

	cmd.Flags().StringVar(&f.qdrantURL, "qdrant-url", "", "External Qdrant URL (implies external mode).")
	cmd.Flags().StringVar(&f.qdrantAPIKey, "qdrant-api-key", "", "API key for the external Qdrant.")
	cmd.Flags().BoolVar(&f.managedQdrant, "managed-qdrant", false, "Run Qdrant as a managed local container.")
	cmd.Flags().BoolVar(&f.externalQdrant, "external-qdrant", false, "Use the Qdrant URL provided via --qdrant-url.")

	cmd.Flags().StringVar(&f.ollamaURL, "ollama-url", "", "Ollama base URL (default http://host.docker.internal:11434).")
	cmd.Flags().StringVar(&f.embeddingModel, "embedding-model", "", "Ollama embedding model name.")
	cmd.Flags().IntVar(&f.embeddingDim, "embedding-dim", 0, "Embedding vector dimension; must match the model.")

	cmd.Flags().StringVar(&f.host, "host", "", "MCP bind host (default 127.0.0.1).")
	cmd.Flags().IntVar(&f.port, "port", 0, "TLS listener port (default 8443).")
	cmd.Flags().IntVar(&f.httpPort, "http-port", 0, "Plain HTTP listener port (default 8080).")
	cmd.Flags().BoolVar(&f.noHTTP, "no-http", false, "Disable the plain-HTTP loopback listener.")

	cmd.Flags().StringVar(&f.certFile, "cert", "", "Path to an existing TLS cert (PEM). Skips self-signed generation.")
	cmd.Flags().StringVar(&f.keyFile, "key", "", "Path to an existing TLS key (PEM). Required with --cert.")

	cmd.Flags().StringVar(&f.collection, "collection", "", "Default Qdrant collection name (default memory).")

	cmd.Flags().BoolVar(&f.nonInteractive, "non-interactive", false, "Skip the wizard; use defaults+flags only.")
	cmd.Flags().BoolVarP(&f.force, "force", "f", false, "Overwrite an existing config.json.")
	cmd.Flags().StringVar(&f.image, "image", "", "Docker image to run for qilin-mcp (default matches this CLI's release).")

	cmd.RunE = func(cmd *cobra.Command, _ []string) error {
		return runInit(cmd, g, f)
	}
	return cmd
}
