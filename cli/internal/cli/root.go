// Package cli wires together the cobra command tree exposed by the qilin binary.
package cli

import (
	"github.com/spf13/cobra"

	"github.com/dev-creations/qilin/cli/internal/version"
)

// Globals holds flags that apply to every subcommand.
type Globals struct {
	QilinHome string
}

// NewRoot returns the top-level `qilin` command with all subcommands attached.
func NewRoot() *cobra.Command {
	g := &Globals{}

	root := &cobra.Command{
		Use:   "qilin",
		Short: "Plug-and-play vector memory over MCP/SSE.",
		Long: "qilin is the control-plane CLI for the Qilin MCP server.\n\n" +
			"It generates per-host config and TLS material under $QILIN_HOME\n" +
			"(default ~/.qilin) and orchestrates the server (and optionally a\n" +
			"managed Qdrant) over Docker.",
		SilenceUsage:  true,
		SilenceErrors: true,
		Version:       version.String(),
	}

	root.PersistentFlags().StringVar(&g.QilinHome, "qilin-home", "",
		"Override the qilin config directory (default $QILIN_HOME or ~/.qilin).")

	root.SetVersionTemplate(version.Long() + "\n")

	root.AddCommand(newVersionCmd())
	root.AddCommand(newInitCmd(g))
	root.AddCommand(newUpCmd(g))
	root.AddCommand(newDownCmd(g))
	root.AddCommand(newStatusCmd(g))
	root.AddCommand(newLogsCmd(g))
	root.AddCommand(newDoctorCmd(g))
	root.AddCommand(newConfigCmd(g))
	root.AddCommand(newCertCmd(g))
	root.AddCommand(newIngestCmd(g))
	root.AddCommand(newRecallCmd(g))

	return root
}
