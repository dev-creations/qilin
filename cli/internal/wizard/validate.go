package wizard

import (
	"fmt"
	"net/url"
	"strconv"
	"strings"
)

func nonEmpty(label string) func(string) error {
	return func(s string) error {
		if strings.TrimSpace(s) == "" {
			return fmt.Errorf("%s must not be empty", label)
		}
		return nil
	}
}

func validatePort(s string) error {
	if _, err := parsePort(s); err != nil {
		return err
	}
	return nil
}

func parsePort(s string) (int, error) {
	n, err := strconv.Atoi(strings.TrimSpace(s))
	if err != nil {
		return 0, fmt.Errorf("must be an integer (got %q)", s)
	}
	if n <= 0 || n > 65535 {
		return 0, fmt.Errorf("port out of range: %d", n)
	}
	return n, nil
}

func requireURLIfExternal(mode *string) func(string) error {
	return func(s string) error {
		if mode == nil || *mode != "external" {
			return nil
		}
		s = strings.TrimSpace(s)
		if s == "" {
			return fmt.Errorf("Qdrant URL is required in external mode")
		}
		u, err := url.Parse(s)
		if err != nil || u.Scheme == "" || u.Host == "" {
			return fmt.Errorf("must be a valid URL (got %q)", s)
		}
		return nil
	}
}
