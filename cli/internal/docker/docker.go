// Package docker is a thin, typed wrapper around the `docker compose` CLI.
//
// We deliberately shell out instead of using the docker Go SDK: it keeps the
// binary small, avoids licensing complexity, and means users see the exact
// command they could re-run by hand if something goes wrong.
package docker

import (
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
)

// Client carries the path to the rendered compose.yaml. All operations are
// scoped to that one project, so this is essentially `docker compose -f X`
// with a few quality-of-life helpers.
type Client struct {
	ComposeFile string
	Stdout      io.Writer
	Stderr      io.Writer
	Stdin       io.Reader
}

// New constructs a Client wired to the calling process's streams.
func New(composeFile string) *Client {
	return &Client{
		ComposeFile: composeFile,
		Stdout:      os.Stdout,
		Stderr:      os.Stderr,
		Stdin:       os.Stdin,
	}
}

// EnsureAvailable checks that `docker` is on PATH and that the `compose`
// subcommand is present. It returns a helpful error message otherwise.
func EnsureAvailable() error {
	if _, err := exec.LookPath("docker"); err != nil {
		return errors.New("docker is required but was not found in PATH; install Docker Desktop or the docker CLI and try again")
	}
	cmd := exec.Command("docker", "compose", "version")
	cmd.Stdout = io.Discard
	cmd.Stderr = io.Discard
	if err := cmd.Run(); err != nil {
		return errors.New("`docker compose` is required (Docker Compose v2 plugin); install it and try again")
	}
	return nil
}

// Up runs `docker compose up`. detached=true adds `-d`.
func (c *Client) Up(detached bool) error {
	args := []string{"compose", "-f", c.ComposeFile, "up"}
	if detached {
		args = append(args, "-d")
	}
	return c.run(args...)
}

// Pull eagerly fetches images so the first `up` doesn't include a long
// silent download.
func (c *Client) Pull() error {
	return c.run("compose", "-f", c.ComposeFile, "pull")
}

// Down stops the project. volumes=true also removes named volumes (e.g.
// qdrant_data), which is destructive for the user's memory.
func (c *Client) Down(volumes bool) error {
	args := []string{"compose", "-f", c.ComposeFile, "down"}
	if volumes {
		args = append(args, "-v")
	}
	return c.run(args...)
}

// PS runs `docker compose ps` with a fixed column set.
func (c *Client) PS() error {
	return c.run("compose", "-f", c.ComposeFile, "ps")
}

// Logs streams `docker compose logs`. follow=true tails new output.
func (c *Client) Logs(service string, follow bool) error {
	args := []string{"compose", "-f", c.ComposeFile, "logs"}
	if follow {
		args = append(args, "-f")
	}
	if service != "" {
		args = append(args, service)
	}
	return c.run(args...)
}

// Exec runs `docker compose exec <service> <cmd...>`. interactive=true keeps
// a TTY open (used by ingest/recall proxy commands).
func (c *Client) Exec(service string, interactive bool, cmd ...string) error {
	args := []string{"compose", "-f", c.ComposeFile, "exec"}
	if !interactive {
		args = append(args, "-T")
	}
	args = append(args, service)
	args = append(args, cmd...)
	return c.run(args...)
}

func (c *Client) run(args ...string) error {
	cmd := exec.Command("docker", args...)
	cmd.Stdout = c.Stdout
	cmd.Stderr = c.Stderr
	cmd.Stdin = c.Stdin
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("docker %s: %w", joinArgs(args), err)
	}
	return nil
}

func joinArgs(args []string) string {
	if len(args) == 0 {
		return ""
	}
	out := args[0]
	for _, a := range args[1:] {
		out += " " + a
	}
	return out
}
