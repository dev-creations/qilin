package cli

import (
	"context"
	"fmt"

	"github.com/spf13/cobra"

	"github.com/dev-creations/qilin/cli/internal/config"
	"github.com/dev-creations/qilin/cli/internal/doctor"
	"github.com/dev-creations/qilin/cli/internal/paths"
)

func newDoctorCmd(g *Globals) *cobra.Command {
	return &cobra.Command{
		Use:   "doctor",
		Short: "Run a sanity check across Qdrant, Ollama, TLS, and Docker.",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return runDoctor(cmd, g)
		},
	}
}

func runDoctor(cmd *cobra.Command, g *Globals) error {
	out := cmd.OutOrStdout()

	layout, err := paths.Resolve(g.QilinHome)
	if err != nil {
		return err
	}
	if !layout.ConfigExists() {
		return fmt.Errorf("qilin is not configured yet — run `qilin init` first (looked at %s)", layout.Config)
	}
	cfg, err := config.Load(layout.Config)
	if err != nil {
		return err
	}

	rep := doctor.Run(context.Background(), doctor.Input{
		QdrantManaged: cfg.Qdrant.Managed,
		QdrantURL:     cfg.Qdrant.URL,
		OllamaURL:     cfg.Ollama.URL,
		CertPath:      cfg.TLS.CertFile,
	})

	for _, c := range rep.Checks {
		fmt.Fprintf(out, "  [%s] %-8s %s\n", c.Status, c.Name, c.Message)
	}
	if rep.Worst() == doctor.StatusFail {
		return fmt.Errorf("one or more checks failed")
	}
	return nil
}
