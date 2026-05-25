// Package tls generates self-signed X.509 material for the Qilin MCP server.
//
// Mirrors the OpenSSL invocation in scripts/entrypoint.sh (RSA 4096, SHA-256,
// 10-year validity, SAN list covering localhost) but in pure Go so it works
// on Windows hosts without bundling OpenSSL.
package tls

import (
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"errors"
	"fmt"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"time"
)

// Options controls cert generation. Zero values pick sensible defaults that
// match the existing container entrypoint behavior.
type Options struct {
	CommonName   string        // default: "qilin"
	Organization string        // default: "Qilin"
	KeyBits      int           // default: 4096
	Validity     time.Duration // default: 10 years
	DNSNames     []string      // default: ["localhost", "qilin", "qilin-mcp"]
	IPAddresses  []net.IP      // default: [127.0.0.1, ::1]

	// Clock is injected by tests to make NotBefore/NotAfter deterministic.
	// Production callers leave it nil and time.Now is used.
	Clock func() time.Time
}

// Materials holds the PEM-encoded outputs of GenerateSelfSigned.
type Materials struct {
	CertPEM []byte
	KeyPEM  []byte
}

// GenerateSelfSigned creates a fresh RSA private key and a matching X.509
// certificate that signs itself. It returns the encoded PEM bytes so callers
// can decide whether to write them to disk (WritePair) or hold them in memory.
func GenerateSelfSigned(opts Options) (*Materials, error) {
	now := time.Now
	if opts.Clock != nil {
		now = opts.Clock
	}

	keyBits := opts.KeyBits
	if keyBits == 0 {
		keyBits = 4096
	}
	if keyBits < 2048 {
		return nil, fmt.Errorf("KeyBits=%d is too small; use at least 2048", keyBits)
	}

	validity := opts.Validity
	if validity == 0 {
		validity = 10 * 365 * 24 * time.Hour
	}

	cn := opts.CommonName
	if cn == "" {
		cn = "qilin"
	}
	org := opts.Organization
	if org == "" {
		org = "Qilin"
	}

	dnsNames := opts.DNSNames
	if dnsNames == nil {
		dnsNames = []string{"localhost", "qilin", "qilin-mcp"}
	}

	ipAddresses := opts.IPAddresses
	if ipAddresses == nil {
		ipAddresses = []net.IP{
			net.ParseIP("127.0.0.1"),
			net.ParseIP("::1"),
		}
	}

	key, err := rsa.GenerateKey(rand.Reader, keyBits)
	if err != nil {
		return nil, fmt.Errorf("generate RSA key: %w", err)
	}

	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		return nil, fmt.Errorf("generate serial: %w", err)
	}

	notBefore := now().Add(-5 * time.Minute).UTC() // tolerate small clock skew
	notAfter := now().Add(validity).UTC()

	tmpl := &x509.Certificate{
		SerialNumber: serial,
		Subject: pkix.Name{
			CommonName:   cn,
			Organization: []string{org},
		},
		NotBefore:             notBefore,
		NotAfter:              notAfter,
		KeyUsage:              x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment | x509.KeyUsageCertSign,
		ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth, x509.ExtKeyUsageClientAuth},
		BasicConstraintsValid: true,
		IsCA:                  true,
		DNSNames:              dnsNames,
		IPAddresses:           ipAddresses,
	}

	der, err := x509.CreateCertificate(rand.Reader, tmpl, tmpl, &key.PublicKey, key)
	if err != nil {
		return nil, fmt.Errorf("create certificate: %w", err)
	}

	certPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})

	keyDER, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		return nil, fmt.Errorf("marshal private key: %w", err)
	}
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: keyDER})

	return &Materials{CertPEM: certPEM, KeyPEM: keyPEM}, nil
}

// WritePair writes m to certPath and keyPath, creating parent dirs and
// applying 0600 perms to the key. Existing files are overwritten.
func (m *Materials) WritePair(certPath, keyPath string) error {
	if m == nil {
		return errors.New("tls: WritePair called with nil materials")
	}
	for _, p := range []string{certPath, keyPath} {
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			return fmt.Errorf("mkdir %s: %w", filepath.Dir(p), err)
		}
	}
	if err := os.WriteFile(certPath, m.CertPEM, 0o644); err != nil {
		return fmt.Errorf("write cert %s: %w", certPath, err)
	}
	if err := os.WriteFile(keyPath, m.KeyPEM, 0o600); err != nil {
		return fmt.Errorf("write key %s: %w", keyPath, err)
	}
	return nil
}

// InspectFile returns metadata about an on-disk PEM cert, used by
// `qilin cert show` and `qilin doctor`.
func InspectFile(path string) (*Info, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", path, err)
	}
	block, _ := pem.Decode(raw)
	if block == nil {
		return nil, fmt.Errorf("%s is not PEM-encoded", path)
	}
	cert, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		return nil, fmt.Errorf("parse certificate: %w", err)
	}
	return &Info{
		Path:        path,
		Subject:     cert.Subject.String(),
		Issuer:      cert.Issuer.String(),
		NotBefore:   cert.NotBefore,
		NotAfter:    cert.NotAfter,
		DNSNames:    cert.DNSNames,
		IPAddresses: cert.IPAddresses,
		SelfSigned:  cert.Issuer.String() == cert.Subject.String(),
	}, nil
}

// Info is the human-readable summary returned by InspectFile.
type Info struct {
	Path        string
	Subject     string
	Issuer      string
	NotBefore   time.Time
	NotAfter    time.Time
	DNSNames    []string
	IPAddresses []net.IP
	SelfSigned  bool
}

// Expired reports whether the cert is currently outside its validity window.
func (i *Info) Expired(now time.Time) bool {
	return now.Before(i.NotBefore) || now.After(i.NotAfter)
}

// DaysUntilExpiry returns a (possibly negative) day count to NotAfter.
func (i *Info) DaysUntilExpiry(now time.Time) int {
	return int(i.NotAfter.Sub(now).Hours() / 24)
}
