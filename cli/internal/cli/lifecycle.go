package cli

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/dev-creations/qilin/cli/internal/docker"
	"github.com/dev-creations/qilin/cli/internal/paths"
)

func newUpCmd(g *Globals) *cobra.Command {
	var detach bool
	var pull bool
	cmd := &cobra.Command{
		Use:   "up",
		Short: "Start the Qilin MCP server (and managed Qdrant if configured).",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			cli, err := dockerClientFromGlobals(g)
			if err != nil {
				return err
			}
			if pull {
				if err := cli.Pull(); err != nil {
					return err
				}
			}
			return cli.Up(detach)
		},
	}
	cmd.Flags().BoolVarP(&detach, "detach", "d", true, "Run containers in the background.")
	cmd.Flags().BoolVar(&pull, "pull", false, "Pull the latest images before starting.")
	return cmd
}

func newDownCmd(g *Globals) *cobra.Command {
	var withVolumes bool
	cmd := &cobra.Command{
		Use:   "down",
		Short: "Stop the Qilin MCP server.",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			cli, err := dockerClientFromGlobals(g)
			if err != nil {
				return err
			}
			return cli.Down(withVolumes)
		},
	}
	cmd.Flags().BoolVar(&withVolumes, "volumes", false,
		"Also remove the qdrant_data volume (destructive: wipes all stored vectors).")
	return cmd
}

func newStatusCmd(g *Globals) *cobra.Command {
	return &cobra.Command{
		Use:   "status",
		Short: "Show the running status of the Qilin stack.",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			cli, err := dockerClientFromGlobals(g)
			if err != nil {
				return err
			}
			return cli.PS()
		},
	}
}

func newLogsCmd(g *Globals) *cobra.Command {
	var follow bool
	cmd := &cobra.Command{
		Use:   "logs [service]",
		Short: "Tail logs from a Qilin service.",
		Long: "Stream container logs. With no argument, shows logs from all services;\n" +
			"pass `qilin-mcp` or `qdrant` to filter.",
		Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			cli, err := dockerClientFromGlobals(g)
			if err != nil {
				return err
			}
			service := ""
			if len(args) == 1 {
				service = args[0]
			}
			return cli.Logs(service, follow)
		},
	}
	cmd.Flags().BoolVarP(&follow, "follow", "f", false, "Tail logs (equivalent to docker compose logs -f).")
	return cmd
}

// dockerClientFromGlobals resolves QILIN_HOME, verifies docker is installed,
// and ensures `qilin init` has been run. Shared by every lifecycle command so
// the error UX is consistent.
func dockerClientFromGlobals(g *Globals) (*docker.Client, error) {
	layout, err := paths.Resolve(g.QilinHome)
	if err != nil {
		return nil, err
	}
	if !layout.ConfigExists() {
		return nil, fmt.Errorf(
			"qilin is not configured for this host yet — run `qilin init` first (looked at %s)",
			layout.Config,
		)
	}
	if err := docker.EnsureAvailable(); err != nil {
		return nil, err
	}
	return docker.New(layout.Compose), nil
}
