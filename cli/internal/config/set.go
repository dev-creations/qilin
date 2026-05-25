package config

import (
	"fmt"
	"strconv"
	"strings"
)

// Set assigns value to the field referenced by the dotted key (e.g.
// "server.port", "qdrant.url"). It returns an error for unknown keys or
// type-mismatched values, so `qilin config set` can give a useful message
// instead of silently corrupting the file.
//
// Keeping this as a hand-written switch (rather than reflection) is verbose
// but a) catches typos at compile time and b) lets us reject keys like
// "schema_version" that users should not be editing directly.
func (c *Config) Set(key, value string) error {
	switch strings.ToLower(strings.TrimSpace(key)) {
	case "image":
		c.Image = value
	case "default_collection", "collection":
		c.Collection = value

	case "qdrant.url":
		c.Qdrant.URL = value
		if value != "" {
			c.Qdrant.Managed = false
		}
	case "qdrant.api_key":
		c.Qdrant.APIKey = value
	case "qdrant.managed":
		b, err := strconv.ParseBool(value)
		if err != nil {
			return fmt.Errorf("qdrant.managed must be a boolean: %w", err)
		}
		c.Qdrant.Managed = b
	case "qdrant.image_tag":
		c.Qdrant.ImageTag = value
	case "qdrant.host_port":
		p, err := parsePort("qdrant.host_port", value)
		if err != nil {
			return err
		}
		c.Qdrant.HostPort = p

	case "ollama.url":
		c.Ollama.URL = value
	case "ollama.embedding_model":
		c.Ollama.EmbeddingModel = value
	case "ollama.embedding_dim":
		n, err := strconv.Atoi(value)
		if err != nil || n <= 0 {
			return fmt.Errorf("ollama.embedding_dim must be a positive integer (got %q)", value)
		}
		c.Ollama.EmbeddingDim = n

	case "server.host":
		c.Server.Host = value
	case "server.port":
		p, err := parsePort("server.port", value)
		if err != nil {
			return err
		}
		c.Server.Port = p
	case "server.http_enabled":
		b, err := strconv.ParseBool(value)
		if err != nil {
			return fmt.Errorf("server.http_enabled must be a boolean: %w", err)
		}
		c.Server.HTTPEnabled = b
	case "server.http_port":
		p, err := parsePort("server.http_port", value)
		if err != nil {
			return err
		}
		c.Server.HTTPPort = p

	case "tls.cert_file":
		c.TLS.CertFile = value
	case "tls.key_file":
		c.TLS.KeyFile = value
	case "tls.self_signed":
		b, err := strconv.ParseBool(value)
		if err != nil {
			return fmt.Errorf("tls.self_signed must be a boolean: %w", err)
		}
		c.TLS.SelfSigned = b

	case "chunking.size_tokens":
		n, err := strconv.Atoi(value)
		if err != nil {
			return fmt.Errorf("chunking.size_tokens must be an integer (got %q)", value)
		}
		c.Chunking.SizeTokens = n
	case "chunking.overlap_tokens":
		n, err := strconv.Atoi(value)
		if err != nil {
			return fmt.Errorf("chunking.overlap_tokens must be an integer (got %q)", value)
		}
		c.Chunking.OverlapTokens = n
	case "chunking.batch_size":
		n, err := strconv.Atoi(value)
		if err != nil {
			return fmt.Errorf("chunking.batch_size must be an integer (got %q)", value)
		}
		c.Chunking.BatchSize = n

	case "schema_version":
		return fmt.Errorf("schema_version is managed by qilin and must not be set manually")
	default:
		return fmt.Errorf("unknown config key %q", key)
	}
	return nil
}

func parsePort(name, value string) (int, error) {
	n, err := strconv.Atoi(value)
	if err != nil {
		return 0, fmt.Errorf("%s must be an integer (got %q)", name, value)
	}
	if n <= 0 || n > 65535 {
		return 0, fmt.Errorf("%s out of range: %d", name, n)
	}
	return n, nil
}
