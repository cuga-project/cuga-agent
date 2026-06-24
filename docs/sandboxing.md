# Code Execution Sandboxing

[← Back to README](../README.md)

CUGA can execute generated code locally (default), in a Docker/Podman container, or in an [E2B](https://e2b.dev) cloud sandbox.

## Running with a secure code sandbox (Docker/Podman)

Cuga supports isolated code execution using Docker/Podman containers for enhanced security.

1. **Install container runtime**: Download and install [Rancher Desktop](https://rancherdesktop.io/) or Docker.

2. **Install sandbox dependencies**:

   ```bash
   uv sync --group sandbox
   ```

3. **Start with remote sandbox enabled**:

   ```bash
   cuga start demo --sandbox
   ```

   This automatically configures Cuga to use Docker/Podman for code execution instead of local execution.

4. **Test your sandbox setup** (optional):

   ```bash
   # Test local sandbox (default)
   cuga test-sandbox

   # Test remote sandbox with Docker/Podman
   cuga test-sandbox --remote
   ```

   You should see the output: `('test succeeded\n', {})`

**Note**: Without the `--sandbox` flag, Cuga uses local Python execution (default), which is faster but provides less isolation.

## Running with E2B Cloud Sandbox

CUGA supports [E2B](https://e2b.dev) for cloud-based code execution in secure, ephemeral sandboxes. This provides better isolation than local execution while being faster than Docker/Podman containers.

### Prerequisites:

1. **Get an E2B API key**:
   - Sign up at [e2b.dev](https://e2b.dev)
   - Create an API key from your [dashboard](https://e2b.dev/dashboard)

2. **Set up the E2B template**:
   ```bash
   # Install E2B CLI
   npm install -g @e2b/cli

   # Login with your API key
   e2b auth login

   # Create a template (one-time setup)
   # This creates a 'cuga-langchain' template that CUGA uses
   e2b template build --name cuga-langchain
   ```

3. **Install E2B dependencies**:
   ```bash
   uv sync --group e2b
   ```

4. **Configure environment**:
   Add to your `.env` file:
   ```env
   E2B_API_KEY=your-e2b-api-key-here
   ```

### Exposing Registry to E2B (Required)

E2B runs in the cloud and needs to call your local API registry to execute tools. You need to expose your local registry publicly using a tunneling service like [ngrok](https://ngrok.com).

#### Option 1: Expose Registry Directly (Port 8001)

Best if you have multiple ports available:

```bash
# In a separate terminal, start ngrok tunnel to registry
ngrok http 8001

# You'll get a public URL like: https://abc123.ngrok.io
# Copy this URL
```

Then edit `./src/cuga/settings.toml`:
```toml
[server_ports]
function_call_host = "https://abc123.ngrok.io"  # Your ngrok URL
```

#### Option 2: Expose CUGA Port with Proxy (Port 7860)

Best if you're restricted to 1 port - CUGA will proxy calls to the registry:

```bash
# In a separate terminal, start ngrok tunnel to CUGA
ngrok http 7860

# You'll get a public URL like: https://xyz789.ngrok.io
# Copy this URL
```

Then edit `./src/cuga/settings.toml`:
```toml
[server_ports]
function_call_host = "https://xyz789.ngrok.io"  # Your ngrok URL
```

CUGA automatically proxies `/functions/call` requests to the registry when using the CUGA port.

### Enable E2B in Settings

Edit `./src/cuga/settings.toml`:
```toml
[advanced_features]
e2b_sandbox = true
e2b_sandbox_mode = "per-session"  # Options: "per-session" | "single" | "per-call"
e2b_sandbox_ttl = 600  # Cache TTL in seconds (10 minutes)
```

### Sandbox Modes:

- **`per-session`** (default): One sandbox per conversation thread, cached for reuse
- **`single`**: Single shared sandbox across all threads (most cost-effective)
- **`per-call`**: New sandbox for each execution (most isolated, highest cost)

### Start CUGA with E2B:

```bash
# Make sure ngrok is running in another terminal
cuga start demo
```

E2B will automatically execute code in cloud sandboxes. You'll see logs indicating "CODE SENT TO E2B SANDBOX" when E2B is active.

### Troubleshooting:

- **Error: "function_call_host not configured"**: Make sure you've set `function_call_host` in settings.toml with your ngrok URL
- **Tool execution fails**: Verify ngrok is running and the URL in settings.toml matches your ngrok URL
- **Connection timeout**: Check that your firewall allows ngrok connections

**Benefits of E2B**:
- No Docker/Podman required
- Faster than container-based sandboxing
- Cloud-native with automatic scaling
- Better isolation than local execution
- Supports per-session caching for cost optimization

**Note**: E2B is a paid service with a free tier. Check [e2b.dev/pricing](https://e2b.dev/pricing) for details.
