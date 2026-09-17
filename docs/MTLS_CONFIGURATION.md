# mTLS Configuration Guide

## Overview

gProfiler now supports mutual TLS (mTLS) authentication between the agent and backend server. This feature enables secure, certificate-based authentication in both directions:

- **Server authentication**: Agent verifies the server's identity using a trusted CA
- **Client authentication**: Server verifies the agent's identity using client certificates

This is particularly useful for enterprise deployments requiring strong authentication and encryption.

## Features

### 1. Client Certificate Support

Configure the agent to present a client certificate during TLS handshake:

```bash
gprofiler \
  --upload-results \
  --server-host https://profiler.example.com \
  --tls-client-cert /path/to/client-cert.pem \
  --tls-client-key /path/to/client-key.pem
```

### 2. Custom CA Bundle

Override the system's default CA bundle to verify server certificates:

```bash
gprofiler \
  --upload-results \
  --server-host https://profiler.example.com \
  --tls-ca-bundle /path/to/ca-bundle.pem
```

### 3. Automatic Certificate Refresh

For short-lived certificates (e.g., rotated every 10-12 hours), enable periodic refresh:

```bash
gprofiler \
  --upload-results \
  --server-host https://profiler.example.com \
  --tls-client-cert /path/to/client-cert.pem \
  --tls-client-key /path/to/client-key.pem \
  --tls-cert-refresh-enabled \
  --tls-cert-refresh-interval 21600  # 6 hours in seconds
```

The agent will automatically reload certificates from disk at the specified interval without requiring a restart.

## Configuration Options

### Required for mTLS

| Argument | Description | Example |
|----------|-------------|---------|
| `--tls-client-cert` | Path to client certificate file (PEM format) | `/path/to/client-cert.pem` |
| `--tls-client-key` | Path to client private key file (PEM format) | `/path/to/client-key.pem` |

**Note**: Both `--tls-client-cert` and `--tls-client-key` must be provided together for mTLS to work.

### Optional TLS Configuration

| Argument | Description | Default |
|----------|-------------|---------|
| `--tls-ca-bundle` | Path to CA bundle for server verification (PEM format) | System default CA bundle |
| `--no-verify` | Disable SSL certificate verification (not recommended for production) | SSL verification enabled |

### Certificate Refresh Options

| Argument | Description | Default |
|----------|-------------|---------|
| `--tls-cert-refresh-enabled` | Enable periodic certificate refresh | Disabled |
| `--tls-cert-refresh-interval` | Refresh interval in seconds | 21600 (6 hours) |

## Use Cases

### 1. Standard mTLS with Long-Lived Certificates

For deployments with certificates that don't rotate frequently:

```bash
gprofiler \
  --upload-results \
  --server-host https://profiler.example.com \
  --token <your-token> \
  --service-name <service-name> \
  --tls-client-cert /etc/ssl/certs/gprofiler-client.pem \
  --tls-client-key /etc/ssl/private/gprofiler-client-key.pem \
  --tls-ca-bundle /etc/ssl/certs/ca-bundle.pem
```

### 2. mTLS with Short-Lived Certificates

For PKI systems that issue short-lived certificates (e.g., 10-12 hour validity):

```bash
gprofiler \
  --upload-results \
  --server-host https://profiler.example.com \
  --token <your-token> \
  --service-name <service-name> \
  --tls-client-cert /var/run/pki/client-cert.pem \
  --tls-client-key /var/run/pki/client-key.pem \
  --tls-ca-bundle /var/run/pki/ca-root.pem \
  --tls-cert-refresh-enabled \
  --tls-cert-refresh-interval 21600
```

The agent will reload certificates every 6 hours, ensuring uninterrupted operation even as certificates rotate.

### 3. Development/Testing with Self-Signed Certificates

For local development or testing environments:

```bash
gprofiler \
  --upload-results \
  --server-host https://localhost:8083 \
  --token dev-token \
  --service-name dev-service \
  --tls-ca-bundle /path/to/self-signed-ca.pem \
  --no-verify  # Only for development - skip hostname verification
```

## Server-Side Configuration

The backend server (nginx, Apache, etc.) must be configured to:

1. **Present a valid server certificate** that chains to a CA trusted by the agent
2. **Request and verify client certificates** using a CA bundle that includes the CA that signed the agent's client certificate
3. **(Optional) Pass client identity to the application** via headers for authorization/auditing

### Example nginx Configuration

```nginx
server {
    listen 443 ssl;
    server_name profiler.example.com;
    
    # Server's own certificate
    ssl_certificate /path/to/server-cert.pem;
    ssl_certificate_key /path/to/server-key.pem;
    
    # Client certificate verification (mTLS)
    ssl_client_certificate /path/to/ca-bundle.pem;
    ssl_verify_client on;
    ssl_verify_depth 2;
    
    # Optional: Pass client identity to backend
    proxy_set_header X-Client-DN $ssl_client_s_dn;
    proxy_set_header X-Client-Cert $ssl_client_cert;
    
    location / {
        proxy_pass http://backend:8000;
    }
}
```

## Certificate Management

### Certificate Requirements

- **Format**: PEM (Privacy Enhanced Mail)
- **Client certificate**: Must be trusted by the server's CA bundle
- **Server certificate**: Must be trusted by the agent's CA bundle (or system default)
- **Private keys**: Must be readable by the gProfiler process

### Certificate Rotation

For systems with rotating certificates:

1. **Update certificates on disk** at their mount point
2. **Enable automatic refresh** with `--tls-cert-refresh-enabled`
3. **Set refresh interval** to be less than certificate validity period (e.g., refresh every 6 hours for 12-hour certificates)

The agent will:
- Automatically reload certificates at the specified interval
- Continue operating without restart
- Log certificate refresh events for monitoring

### Monitoring Certificate Refresh

When certificate refresh is enabled, the agent logs:

```
[INFO] ProfilerAPIClient: TLS session refreshed successfully
[INFO] HeartbeatClient: TLS session refreshed successfully
```

If refresh fails, errors are logged but the agent preserves the existing working session and continues operating normally until the next refresh attempt:

```
[ERROR] ProfilerAPIClient: Failed to refresh TLS session: [error details]. Will retry on next interval.
```

**Important**: Certificate refresh failures do not interrupt agent connectivity. The agent continues using its current valid session and will automatically retry on the next refresh interval. This ensures uninterrupted profiling even if temporary issues prevent certificate reload (e.g., file system errors, permission issues, or malformed new certificates).

## Security Considerations

### Best Practices

1. **Protect private keys**: Ensure client private keys have restricted permissions (e.g., `chmod 600`)
2. **Use strong certificates**: Prefer certificates with at least 2048-bit RSA or 256-bit ECDSA keys
3. **Enable verification**: Avoid `--no-verify` in production environments
4. **Monitor expiration**: Set up alerts for certificate expiration
5. **Rotate regularly**: Use short-lived certificates when possible and enable automatic refresh

### What's Protected

With mTLS enabled:

- ✅ **Confidentiality**: All traffic encrypted with TLS 1.2+
- ✅ **Authentication**: Both client and server identities verified via certificates
- ✅ **Integrity**: Data cannot be tampered with in transit
- ✅ **Authorization**: Server can authorize clients based on certificate attributes

### What's NOT Protected

- ❌ **Private key compromise**: If private keys are stolen, an attacker can impersonate the agent
- ❌ **Host compromise**: If the agent host is compromised, certificates can be extracted
- ❌ **Network metadata**: Connection metadata (IPs, timing) may still be visible to network observers

## Troubleshooting

### Common Issues

#### "certificate verify failed: Hostname mismatch"

**Cause**: Server certificate doesn't include the hostname you're connecting to in its Subject Alternative Names (SANs).

**Solutions**:
- Connect using a hostname that's in the certificate's SANs
- Add the hostname to your `/etc/hosts` file mapping to the server IP
- Use `--no-verify` for development only (not recommended for production)

#### "The SSL certificate error" (nginx 400 error)

**Cause**: Server rejected the client certificate.

**Solutions**:
- Verify the server's `ssl_client_certificate` directive points to the correct CA bundle
- Ensure `ssl_verify_client on` is configured
- Check that the client certificate is signed by a CA trusted by the server

#### "SSLError: [SSL: TLSV1_ALERT_UNKNOWN_CA]"

**Cause**: Server certificate is signed by a CA not trusted by the agent.

**Solutions**:
- Use `--tls-ca-bundle` to specify the correct CA bundle
- Add the server's CA to the system trust store
- Verify the server certificate chain is complete

#### Certificate refresh not working

**Cause**: Refresh feature not enabled or interval too long.

**Solutions**:
- Ensure `--tls-cert-refresh-enabled` is set
- Verify certificate files are being updated on disk
- Check agent logs for refresh errors
- Reduce refresh interval if certificates expire before refresh

## Performance Impact

### Resource Usage

- **Memory**: Minimal overhead (~100-200 KB per HTTP client for certificate storage)
- **CPU**: Negligible impact from periodic refresh (runs in background thread)
- **Network**: No additional network overhead

### Refresh Timing

- Certificate refresh runs in a background thread
- Does not block profiling operations
- New connections use refreshed certificates immediately
- Old connections complete gracefully
- **Failure handling**: If refresh fails, the agent preserves its current working session and retries on the next interval, ensuring continuous operation

## Configuration File Support

All TLS options can be specified in the configuration file (`/etc/gprofiler/config.ini`):

```ini
[DEFAULT]
upload-results = true
server-host = https://profiler.example.com
token = your-token
service-name = your-service

# TLS/mTLS Configuration
tls-client-cert = /path/to/client-cert.pem
tls-client-key = /path/to/client-key.pem
tls-ca-bundle = /path/to/ca-bundle.pem
tls-cert-refresh-enabled = true
tls-cert-refresh-interval = 21600
```

## Additional Resources

- [gProfiler README](../README.md) - Main documentation
- [Architecture Overview](ARCHITECTURE.md) - System architecture
- RFC 8446 - The Transport Layer Security (TLS) Protocol Version 1.3
- RFC 5280 - X.509 Certificate and CRL Profile
