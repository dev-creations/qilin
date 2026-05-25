// Command qilin is the user-facing CLI binary for the Qilin MCP server.
//
// See ./internal/cli for the full command tree.
package main

import (
	"fmt"
	"os"

	"github.com/dev-creations/qilin/cli/internal/cli"
)

func main() {
	if err := cli.NewRoot().Execute(); err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}
