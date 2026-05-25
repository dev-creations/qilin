package cli

import (
	"fmt"
	"time"

	"github.com/spf13/cobra"

	"github.com/dev-creations/qilin/cli/internal/config"
	qtls "github.com/dev-creations/qilin/cli/internal/tls"
)

func newCertCmd(g *Globals) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "cert",
		Short: "Inspect or regenerate the local TLS certificate.",
	}
	cmd.AddCommand(
		&cobra.Command{
			Use:   "show",
			Short: "Print metadata about the current cert.",
			Args:  cobra.NoArgs,
			RunE:  func(cmd *cobra.Command, _ []string) error { return runCertShow(cmd, g) },
		},
		&cobra.Command{
			Use:   "path",
			Short: "Print the path of the current cert file.",
			Args:  cobra.NoArgs,
			RunE:  func(cmd *cobra.Command, _ []string) error { return runCertPath(cmd, g) },
		},
		&cobra.Command{
			Use:   "regenerate",
			Short: "Generate a fresh self-signed cert (overwrites the existing one).",
			Args:  cobra.NoArgs,
			RunE:  func(cmd *cobra.Command, _ []string) error { return runCertRegenerate(cmd, g) },
		},
	)
	return cmd
}

func runCertShow(cmd *cobra.Command, g *Globals) error {
	cfg, _, err := loadConfig(g)
	if err != nil {
		return err
	}
	info, err := qtls.InspectFile(cfg.TLS.CertFile)
	if err != nil {
		return err
	}
	now := time.Now()
	out := cmd.OutOrStdout()
	fmt.Fprintf(out, "  path:        %s\n", info.Path)
	fmt.Fprintf(out, "  self-signed: %v\n", info.SelfSigned)
	fmt.Fprintf(out, "  subject:     %s\n", info.Subject)
	fmt.Fprintf(out, "  issuer:      %s\n", info.Issuer)
	fmt.Fprintf(out, "  not before:  %s\n", info.NotBefore.Format(time.RFC3339))
	fmt.Fprintf(out, "  not after:   %s\n", info.NotAfter.Format(time.RFC3339))
	fmt.Fprintf(out, "  dns names:   %v\n", info.DNSNames)
	fmt.Fprintf(out, "  ip sans:     %v\n", info.IPAddresses)
	if info.Expired(now) {
		fmt.Fprintln(out, "  status:      EXPIRED — run `qilin cert regenerate`")
	} else {
		fmt.Fprintf(out, "  status:      valid (%d days remaining)\n", info.DaysUntilExpiry(now))
	}
	return nil
}

func runCertPath(cmd *cobra.Command, g *Globals) error {
	cfg, _, err := loadConfig(g)
	if err != nil {
		return err
	}
	fmt.Fprintln(cmd.OutOrStdout(), cfg.TLS.CertFile)
	return nil
}

func runCertRegenerate(cmd *cobra.Command, g *Globals) error {
	cfg, layout, err := loadConfig(g)
	if err != nil {
		return err
	}
	if !cfg.TLS.SelfSigned {
		return fmt.Errorf(
			"refusing to regenerate: tls.self_signed=false in config; "+
				"qilin only manages certs it generated itself (cert at %s)",
			cfg.TLS.CertFile,
		)
	}
	mat, err := qtls.GenerateSelfSigned(qtls.Options{})
	if err != nil {
		return fmt.Errorf("generate cert: %w", err)
	}
	if err := mat.WritePair(layout.Cert, layout.Key); err != nil {
		return fmt.Errorf("write cert: %w", err)
	}
	// Keep self_signed and paths in sync (paranoia: in case someone moved the
	// cert path manually then regenerated).
	cfg.TLS.CertFile = layout.Cert
	cfg.TLS.KeyFile = layout.Key
	cfg.TLS.SelfSigned = true
	if err := config.Save(layout.Config, cfg); err != nil {
		return err
	}
	fmt.Fprintf(cmd.OutOrStdout(), "new cert written to %s\n", layout.Cert)
	fmt.Fprintln(cmd.OutOrStdout(), "restart the server (`qilin down && qilin up`) to pick it up.")
	return nil
}
