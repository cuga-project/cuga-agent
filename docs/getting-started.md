# Install CUGA and run your first task

The installer is prepared for **v0.4.1**. Publish that release before enabling the command on cuga.dev.

```bash
curl -fsSL https://cuga.dev/install.sh | bash
cuga start manager
```

The installer installs or reuses uv, selects Python 3.12, and installs the approved CUGA release in an isolated environment. The wheel includes the frontend. No cloning, virtual environment setup, Node.js, or frontend build is needed. If requested, open a new terminal before running `cuga start manager`.

| System | Requirements |
| --- | --- |
| macOS | Apple Silicon, macOS 14 or newer |
| Linux | x86_64 or aarch64, glibc 2.28 or newer |
| WSL | A Linux distribution meeting the Linux requirements; run inside its Linux terminal |

The installer selects matched, hash-pinned CPU PyTorch wheels. Intel Macs and musl distributions such as Alpine lack compatible wheels for this release. The default manager runs without Docker. Browser automation and optional sandbox integrations have separate setup requirements.

## Configure your provider locally

Open the URL printed by the CLI, then open `/manage`.

1. In **Set up your first agent**, choose OpenAI, OpenRouter, Groq, Ollama, or an OpenAI-compatible private endpoint.
2. Enter a model identifier available to your provider. For Ollama or a private endpoint, enter its base URL. Enter an API key when required.
3. Select **Save and test connection**. CUGA encrypts the credential in its local secret store and saves a reference in the draft. The test sends a short inference request to your selected provider. Check the provider, model, endpoint, and credential if it fails.
4. Select **Try your first task**. In **Try it out**, ask: **What can you help me automate?** Then connect tools and try a task using them.
5. Select **Publish** when your draft is ready. Published versions are used in **Chat**.

Change an existing provider through the manager's **LLM** section. Repeated installation and normal manager restarts preserve configuration and local data.

## Local data

| Item | Default location |
| --- | --- |
| Agent configurations, policies, and encrypted secrets | `~/.local/share/cuga/dbs/cuga.db` |
| Encryption key | `~/.local/share/cuga/secret.key` (permissions `0600`) |
| Workspace | `~/.local/share/cuga/workspace/` |
| Logs | `~/.local/share/cuga/logs/` |
| Knowledge | `~/.local/share/cuga/knowledge/` |
| Isolated environment | Shown by `uv tool dir` |
| Command directory | Shown by `uv tool dir --bin` |

Set `CUGA_DATA_DIR` or `XDG_DATA_HOME` to select another data directory. Existing explicit storage, logging, encryption-key, and provider environment settings take precedence. Back up the database **and encryption key together**; the key decrypts your saved credentials. Keep this directory private.

The manager binds to `127.0.0.1` by default. Enterprise deployments should configure authentication and storage/secret backends through the [configuration guide](readme/configuration-guide.md#authentication-and-access-control).

## Upgrade

Stop the manager and repeat the installation command to install the release approved on cuga.dev. The installer carries compatibility overrides, transitive security constraints, and CPU wheel selection. Local data remains outside the isolated environment.

For controlled rollout, inspect the versioned installer at `https://cuga.dev/install/v0.4.1.sh`. Its source is [scripts/install.sh](../scripts/install.sh), with compatibility requirements in [scripts/install](../scripts/install/).

## Uninstall

Stop the manager and run `uv tool uninstall cuga`. Saved data remains in your data directory. Remove that directory separately if you also want to delete configurations, credentials, logs, and workspace files. uv and its managed Python can remain for other applications.

## Release and publication checks

1. Run `bash scripts/frontend_build.sh`, then `uv build`.
2. Run `python scripts/check_wheel.py dist/cuga-0.4.1-py3-none-any.whl` and `python scripts/smoke_manager_wheel.py dist/cuga-0.4.1-py3-none-any.whl`. The release workflow runs both before PyPI publication.
3. Publish the approved `v0.4.1` tag and wheel. Copy the exact installer into landing-site and record its SHA-256 manifest.
4. Deploy landing-site. Deployment verifies the PyPI release and byte equality with the tagged installer first, then verifies `https://cuga.dev/install.sh` after deployment.
