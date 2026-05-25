package cli

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"

	"github.com/spf13/cobra"

	"github.com/dev-creations/qilin/cli/internal/docker"
	"github.com/dev-creations/qilin/cli/internal/paths"
)

// The proxy commands forward to the Python `qilin` CLI living inside the
// qilin-mcp container, so users get one consistent entrypoint without
// installing Python.
//
// We use DisableFlagParsing so the host CLI doesn't try to interpret flags
// meant for the in-container command (e.g. --include, --exclude).

func newIngestCmd(g *Globals) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "ingest <path> [flags...]",
		Short: "Ingest a directory into Qilin's vector memory.",
		Long: "Walks <path> and embeds each file into the configured Qdrant\n" +
			"collection. Runs inside the qilin-mcp container; <path> on the host\n" +
			"is bind-mounted into the container automatically.\n\n" +
			"All trailing flags are forwarded to the Python CLI; see\n" +
			"`qilin ingest --help` after running `qilin up` for the full list.",
		DisableFlagParsing: true,
		Args:               cobra.MinimumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return runIngest(cmd, g, args)
		},
	}
	return cmd
}

func newRecallCmd(g *Globals) *cobra.Command {
	cmd := &cobra.Command{
		Use:                "recall <query> [flags...]",
		Short:              "Run a similarity search against a collection.",
		DisableFlagParsing: true,
		Args:               cobra.MinimumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return runRecall(cmd, g, args)
		},
	}
	return cmd
}

func runIngest(cmd *cobra.Command, g *Globals, args []string) error {
	if wantsHelp(args) {
		return cmd.Help()
	}
	layout, err := paths.Resolve(g.QilinHome)
	if err != nil {
		return err
	}
	if !layout.ConfigExists() {
		return fmt.Errorf("qilin is not configured yet — run `qilin init` first")
	}
	if err := docker.EnsureAvailable(); err != nil {
		return err
	}

	host, err := filepath.Abs(args[0])
	if err != nil {
		return fmt.Errorf("resolve ingest path: %w", err)
	}
	if _, err := os.Stat(host); err != nil {
		return fmt.Errorf("ingest path %s does not exist: %w", host, err)
	}

	// Replace args[0] with the in-container mount target /repo and rebuild
	// the docker compose run command. We use `run --rm` (not `exec`) so
	// users don't have to run `qilin up` first just to ingest a directory.
	rest := append([]string{"/repo"}, args[1:]...)

	dockerArgs := []string{
		"compose", "-f", layout.Compose,
		"run", "--rm",
		"-v", host + ":/repo:ro",
		"--entrypoint", "qilin",
		"qilin-mcp",
		"ingest",
	}
	dockerArgs = append(dockerArgs, rest...)

	return runForwarded(cmd, dockerArgs)
}

func runRecall(cmd *cobra.Command, g *Globals, args []string) error {
	if wantsHelp(args) {
		return cmd.Help()
	}
	layout, err := paths.Resolve(g.QilinHome)
	if err != nil {
		return err
	}
	if !layout.ConfigExists() {
		return fmt.Errorf("qilin is not configured yet — run `qilin init` first")
	}
	if err := docker.EnsureAvailable(); err != nil {
		return err
	}

	dockerArgs := []string{
		"compose", "-f", layout.Compose,
		"run", "--rm",
		"--entrypoint", "qilin",
		"qilin-mcp",
		"recall",
	}
	dockerArgs = append(dockerArgs, args...)
	return runForwarded(cmd, dockerArgs)
}

// wantsHelp reports whether the user only asked for help on a proxy command,
// in which case we short-circuit to cobra's built-in help text instead of
// trying to invoke docker. This is needed because DisableFlagParsing means
// cobra otherwise hands "--help" straight to RunE.
func wantsHelp(args []string) bool {
	if len(args) != 1 {
		return false
	}
	switch args[0] {
	case "--help", "-h", "help":
		return true
	}
	return false
}

func runForwarded(cmd *cobra.Command, dockerArgs []string) error {
	c := exec.Command("docker", dockerArgs...)
	c.Stdout = cmd.OutOrStdout()
	c.Stderr = cmd.ErrOrStderr()
	c.Stdin = cmd.InOrStdin()
	if err := c.Run(); err != nil {
		return fmt.Errorf("docker compose: %w", err)
	}
	return nil
}
