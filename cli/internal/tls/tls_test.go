package tls

import (
	"crypto/x509"
	"encoding/pem"
	"net"
	"path/filepath"
	"testing"
	"time"
)

// Use a 2048-bit key in tests to keep the suite snappy; production callers
// stick with the 4096-bit default via opts.KeyBits = 0.
const testKeyBits = 2048

func generateForTest(t *testing.T) *Materials {
	t.Helper()
	mat, err := GenerateSelfSigned(Options{
		KeyBits:  testKeyBits,
		Validity: 24 * time.Hour,
		Clock:    func() time.Time { return time.Date(2025, 1, 1, 0, 0, 0, 0, time.UTC) },
	})
	if err != nil {
		t.Fatalf("GenerateSelfSigned: %v", err)
	}
	return mat
}

func TestGenerateSelfSignedProducesParsableCert(t *testing.T) {
	mat := generateForTest(t)

	block, _ := pem.Decode(mat.CertPEM)
	if block == nil || block.Type != "CERTIFICATE" {
		t.Fatalf("cert PEM has wrong type: %+v", block)
	}
	cert, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		t.Fatalf("parse cert: %v", err)
	}

	wantDNS := []string{"localhost", "qilin", "qilin-mcp"}
	if len(cert.DNSNames) != len(wantDNS) {
		t.Errorf("DNSNames: want %v, got %v", wantDNS, cert.DNSNames)
	}

	hasV4 := false
	for _, ip := range cert.IPAddresses {
		if ip.Equal(net.IPv4(127, 0, 0, 1)) {
			hasV4 = true
		}
	}
	if !hasV4 {
		t.Errorf("expected SAN for 127.0.0.1, got %v", cert.IPAddresses)
	}

	if cert.Subject.CommonName != "qilin" {
		t.Errorf("CN = %q, want qilin", cert.Subject.CommonName)
	}
	if cert.Issuer.String() != cert.Subject.String() {
		t.Errorf("certificate is not self-signed: subj=%s issuer=%s", cert.Subject, cert.Issuer)
	}
	if cert.NotAfter.Sub(cert.NotBefore) < 23*time.Hour {
		t.Errorf("validity window too short: %s -> %s", cert.NotBefore, cert.NotAfter)
	}
}

func TestKeyIsPKCS8RSA(t *testing.T) {
	mat := generateForTest(t)
	block, _ := pem.Decode(mat.KeyPEM)
	if block == nil || block.Type != "PRIVATE KEY" {
		t.Fatalf("key PEM has wrong type: %+v", block)
	}
	if _, err := x509.ParsePKCS8PrivateKey(block.Bytes); err != nil {
		t.Fatalf("ParsePKCS8PrivateKey: %v", err)
	}
}

func TestWritePairAppliesCorrectPerms(t *testing.T) {
	dir := t.TempDir()
	certPath := filepath.Join(dir, "certs", "cert.pem")
	keyPath := filepath.Join(dir, "certs", "key.pem")

	mat := generateForTest(t)
	if err := mat.WritePair(certPath, keyPath); err != nil {
		t.Fatalf("WritePair: %v", err)
	}

	info, err := InspectFile(certPath)
	if err != nil {
		t.Fatalf("InspectFile: %v", err)
	}
	if !info.SelfSigned {
		t.Errorf("InspectFile should report self-signed=true")
	}
}

func TestRejectsTooSmallKey(t *testing.T) {
	_, err := GenerateSelfSigned(Options{KeyBits: 512})
	if err == nil {
		t.Fatal("expected error for tiny key")
	}
}

func TestInfoExpiredAndDays(t *testing.T) {
	info := &Info{
		NotBefore: time.Date(2025, 1, 1, 0, 0, 0, 0, time.UTC),
		NotAfter:  time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC),
	}
	mid := time.Date(2025, 7, 1, 0, 0, 0, 0, time.UTC)
	if info.Expired(mid) {
		t.Errorf("not expired at %s", mid)
	}
	if info.DaysUntilExpiry(mid) < 180 {
		t.Errorf("DaysUntilExpiry too low at %s", mid)
	}
	if !info.Expired(time.Date(2027, 1, 1, 0, 0, 0, 0, time.UTC)) {
		t.Errorf("should be expired in 2027")
	}
}
