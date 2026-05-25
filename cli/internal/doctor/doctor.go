// Package doctor runs a battery of "is this host ready for qilin?" checks.
//
// Each Check is independent so failures are reported together (rather than
// stopping at the first one), and each one is cheap and read-only.
package doctor

import (
	"context"
	"crypto/tls"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"os/exec"
	"time"

	qtls "github.com/dev-creations/qilin/cli/internal/tls"
)

// Status is the result of a single check.
type Status int

const (
	StatusOK Status = iota
	StatusWarn
	StatusFail
)

func (s Status) String() string {
	switch s {
	case StatusOK:
		return "OK"
	case StatusWarn:
		return "WARN"
	case StatusFail:
		return "FAIL"
	}
	return "?"
}

// Check captures the outcome of one diagnostic.
type Check struct {
	Name    string
	Status  Status
	Message string
}

// Report bundles every check from a single run.
type Report struct {
	Checks []Check
}

// Worst returns the highest severity in the report.
func (r *Report) Worst() Status {
	worst := StatusOK
	for _, c := range r.Checks {
		if c.Status > worst {
			worst = c.Status
		}
	}
	return worst
}

// Input feeds Run with the values it needs to reach Qdrant and Ollama.
type Input struct {
	QdrantManaged bool
	QdrantURL     string // user-provided URL in external mode
	OllamaURL     string

	// CertPath is the host-side TLS cert path (so doctor can validate it
	// without touching the container).
	CertPath string

	HTTPTimeout time.Duration
}

// Run executes every check sequentially. None of them mutate state.
func Run(ctx context.Context, in Input) Report {
	if in.HTTPTimeout == 0 {
		in.HTTPTimeout = 5 * time.Second
	}
	client := newHTTPClient(in.HTTPTimeout)

	r := Report{}
	r.Checks = append(r.Checks, checkDocker())
	r.Checks = append(r.Checks, checkOllama(ctx, client, in.OllamaURL))
	r.Checks = append(r.Checks, checkQdrant(ctx, client, in))
	r.Checks = append(r.Checks, checkCert(in.CertPath))
	return r
}

func newHTTPClient(timeout time.Duration) *http.Client {
	return &http.Client{
		Timeout: timeout,
		Transport: &http.Transport{
			// Doctor probes self-signed Qdrant/Ollama URLs over plain HTTP by
			// default; for HTTPS we don't care about cert validity at this
			// stage — we're checking reachability, not trust.
			TLSClientConfig: &tls.Config{InsecureSkipVerify: true}, // #nosec G402 -- reachability probe only
		},
	}
}

func checkDocker() Check {
	if _, err := exec.LookPath("docker"); err != nil {
		return Check{Name: "docker", Status: StatusFail, Message: "docker not found in PATH"}
	}
	cmd := exec.Command("docker", "version", "--format", "{{.Server.Version}}")
	out, err := cmd.Output()
	if err != nil {
		return Check{Name: "docker", Status: StatusFail, Message: "docker daemon not reachable (is Docker Desktop running?)"}
	}
	composeCmd := exec.Command("docker", "compose", "version", "--short")
	composeOut, err := composeCmd.Output()
	if err != nil {
		return Check{Name: "docker", Status: StatusFail, Message: "docker compose v2 plugin missing"}
	}
	return Check{Name: "docker", Status: StatusOK, Message: fmt.Sprintf("engine %s, compose %s", trim(out), trim(composeOut))}
}

func checkOllama(ctx context.Context, client *http.Client, base string) Check {
	if base == "" {
		return Check{Name: "ollama", Status: StatusFail, Message: "ollama URL not configured"}
	}
	u, err := url.Parse(base)
	if err != nil {
		return Check{Name: "ollama", Status: StatusFail, Message: "invalid URL: " + err.Error()}
	}

	// host.docker.internal resolves *inside* containers but typically not on
	// the host. When the user kept the default URL we probe localhost on the
	// same port instead, which is what the container will see anyway.
	probe := *u
	if probe.Hostname() == "host.docker.internal" {
		port := probe.Port()
		if port == "" {
			port = "11434"
		}
		probe.Host = "127.0.0.1:" + port
	}

	probe = *probe.ResolveReference(&url.URL{Path: "/api/tags"})
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, probe.String(), nil)
	resp, err := client.Do(req)
	if err != nil {
		msg := "unreachable from this host: " + summarizeErr(err)
		if u.Hostname() == "host.docker.internal" {
			msg += " (probed 127.0.0.1; docker containers resolve host.docker.internal automatically)"
		}
		return Check{Name: "ollama", Status: StatusFail, Message: msg}
	}
	resp.Body.Close()
	if resp.StatusCode >= 200 && resp.StatusCode < 500 {
		return Check{Name: "ollama", Status: StatusOK, Message: "responding at " + probe.Host}
	}
	return Check{Name: "ollama", Status: StatusWarn, Message: fmt.Sprintf("HTTP %d from %s", resp.StatusCode, &probe)}
}

func checkQdrant(ctx context.Context, client *http.Client, in Input) Check {
	if in.QdrantManaged {
		// In managed mode the qdrant container is on the compose network, so
		// it's not reachable from the host until `qilin up`. Probing the
		// loopback HTTP port (default 6333) gives us a hint though.
		return checkQdrantManaged(ctx, client)
	}
	if in.QdrantURL == "" {
		return Check{Name: "qdrant", Status: StatusFail, Message: "external mode but no URL configured"}
	}
	u, err := url.Parse(in.QdrantURL)
	if err != nil {
		return Check{Name: "qdrant", Status: StatusFail, Message: "invalid URL: " + err.Error()}
	}
	u = u.ResolveReference(&url.URL{Path: "/healthz"})
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, u.String(), nil)
	resp, err := client.Do(req)
	if err != nil {
		return Check{Name: "qdrant", Status: StatusFail, Message: "unreachable: " + summarizeErr(err)}
	}
	resp.Body.Close()
	if resp.StatusCode >= 200 && resp.StatusCode < 500 {
		return Check{Name: "qdrant", Status: StatusOK, Message: "responding at " + in.QdrantURL}
	}
	return Check{Name: "qdrant", Status: StatusWarn, Message: fmt.Sprintf("HTTP %d from %s", resp.StatusCode, in.QdrantURL)}
}

func checkQdrantManaged(ctx context.Context, client *http.Client) Check {
	probe := "http://127.0.0.1:6333/healthz"
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, probe, nil)
	resp, err := client.Do(req)
	if err != nil {
		return Check{Name: "qdrant", Status: StatusWarn,
			Message: "managed; container is not running yet (run `qilin up`)"}
	}
	resp.Body.Close()
	return Check{Name: "qdrant", Status: StatusOK, Message: "managed; container responding on 127.0.0.1:6333"}
}

func checkCert(path string) Check {
	if path == "" {
		return Check{Name: "tls", Status: StatusWarn, Message: "no TLS cert configured (init may not have completed)"}
	}
	info, err := qtls.InspectFile(path)
	if err != nil {
		return Check{Name: "tls", Status: StatusFail, Message: err.Error()}
	}
	now := time.Now()
	if info.Expired(now) {
		return Check{Name: "tls", Status: StatusFail, Message: "cert is outside its validity window; run `qilin cert regenerate`"}
	}
	days := info.DaysUntilExpiry(now)
	if days < 30 {
		return Check{Name: "tls", Status: StatusWarn, Message: fmt.Sprintf("expires in %d days", days)}
	}
	return Check{Name: "tls", Status: StatusOK, Message: fmt.Sprintf("self-signed, expires in %d days", days)}
}

func summarizeErr(err error) string {
	if err == nil {
		return ""
	}
	if ue, ok := err.(*url.Error); ok {
		if ne, ok := ue.Err.(*net.OpError); ok {
			return ne.Op + " " + ne.Net + ": " + ne.Err.Error()
		}
	}
	return err.Error()
}

func trim(b []byte) string {
	s := string(b)
	for len(s) > 0 && (s[len(s)-1] == '\n' || s[len(s)-1] == '\r' || s[len(s)-1] == ' ') {
		s = s[:len(s)-1]
	}
	return s
}
