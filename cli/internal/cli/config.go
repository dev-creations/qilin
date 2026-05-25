package cli

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"runtime"

	"github.com/spf13/cobra"

	"github.com/dev-creations/qilin/cli/internal/compose"
	"github.com/dev-creations/qilin/cli/internal/config"
	"github.com/dev-creations/qilin/cli/internal/paths"
)

func newConfigCmd(g *Globals) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "config",
		Short: "Inspect or modify the qilin config file.",
	}
	cmd.AddCommand(
		&cobra.Command{
			Use:   "show",
			Short: "Print the resolved config as JSON.",
			Args:  cobra.NoArgs,
			RunE:  func(cmd *cobra.Command, _ []string) error { return runConfigShow(cmd, g) },
		},
		&cobra.Command{
			Use:   "path",
			Short: "Print the absolute path of the config file.",
			Args:  cobra.NoArgs,
			RunE:  func(cmd *cobra.Command, _ []string) error { return runConfigPath(cmd, g) },
		},
		&cobra.Command{
			Use:   "set <key> <value>",
			Short: "Set a single field in the config file (dotted key path).",
			Args:  cobra.ExactArgs(2),
			RunE:  func(cmd *cobra.Command, args []string) error { return runConfigSet(cmd, g, args[0], args[1]) },
		},
		&cobra.Command{
			Use:   "edit",
			Short: "Open the config file in $EDITOR (or notepad/vi).",
			Args:  cobra.NoArgs,
			RunE:  func(cmd *cobra.Command, _ []string) error { return runConfigEdit(cmd, g) },
		},
	)
	return cmd
}

func runConfigShow(cmd *cobra.Command, g *Globals) error {
	cfg, _, err := loadConfig(g)
	if err != nil {
		return err
	}
	data, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return err
	}
	fmt.Fprintln(cmd.OutOrStdout(), string(data))
	return nil
}

func runConfigPath(cmd *cobra.Command, g *Globals) error {
	layout, err := paths.Resolve(g.QilinHome)
	if err != nil {
		return err
	}
	fmt.Fprintln(cmd.OutOrStdout(), layout.Config)
	return nil
}

// runConfigSet mutates the config file in place and re-renders the .env and
// compose.yaml so the on-disk artifacts stay in sync. Running `qilin up`
// after a `qilin config set` should "just work" without a manual re-init.
func runConfigSet(cmd *cobra.Command, g *Globals, key, value string) error {
	cfg, layout, err := loadConfig(g)
	if err != nil {
		return err
	}
	if err := cfg.Set(key, value); err != nil {
		return err
	}
	if err := cfg.Validate(); err != nil {
		return fmt.Errorf("invalid config after set: %w", err)
	}
	if err := config.Save(layout.Config, cfg); err != nil {
		return err
	}
	if err := os.WriteFile(layout.Env, []byte(cfg.EnvFile(containerCertPath, containerKeyPath)), 0o600); err != nil {
		return fmt.Errorf("rewrite .env: %w", err)
	}
	body, err := compose.Render(cfg, layout.Certs, layout.Data, layout.Env, cfg.Image)
	if err != nil {
		return err
	}
	if err := os.WriteFile(layout.Compose, body, 0o644); err != nil {
		return fmt.Errorf("rewrite compose.yaml: %w", err)
	}
	fmt.Fprintf(cmd.OutOrStdout(), "updated %s = %s\n", key, value)
	return nil
}

func runConfigEdit(cmd *cobra.Command, g *Globals) error {
	layout, err := paths.Resolve(g.QilinHome)
	if err != nil {
		return err
	}
	if !layout.ConfigExists() {
		return fmt.Errorf("qilin is not configured yet — run `qilin init` first")
	}
	editor := pickEditor()
	if editor == "" {
		return fmt.Errorf("no editor configured; set $EDITOR or $VISUAL")
	}
	c := exec.Command(editor, layout.Config)
	c.Stdin = cmd.InOrStdin()
	c.Stdout = cmd.OutOrStdout()
	c.Stderr = cmd.ErrOrStderr()
	if err := c.Run(); err != nil {
		return fmt.Errorf("editor exited with error: %w", err)
	}
	// Re-validate after editing so a typo is caught now instead of at `qilin up`.
	cfg, err := config.Load(layout.Config)
	if err != nil {
		return err
	}
	if err := cfg.Validate(); err != nil {
		return fmt.Errorf("edited config is invalid: %w", err)
	}
	return nil
}

func pickEditor() string {
	for _, env := range []string{"VISUAL", "EDITOR"} {
		if v := os.Getenv(env); v != "" {
			return v
		}
	}
	if runtime.GOOS == "windows" {
		return "notepad"
	}
	for _, candidate := range []string{"nano", "vim", "vi"} {
		if _, err := exec.LookPath(candidate); err == nil {
			return candidate
		}
	}
	return ""
}

// loadConfig is a tiny helper shared by every config subcommand.
func loadConfig(g *Globals) (*config.Config, paths.Layout, error) {
	layout, err := paths.Resolve(g.QilinHome)
	if err != nil {
		return nil, paths.Layout{}, err
	}
	if !layout.ConfigExists() {
		return nil, layout, fmt.Errorf("qilin is not configured yet — run `qilin init` first (looked at %s)", layout.Config)
	}
	cfg, err := config.Load(layout.Config)
	if err != nil {
		return nil, layout, err
	}
	return cfg, layout, nil
}
