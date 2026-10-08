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

The installer selects matched, hash-pinned CPU PyTorch wheels. On Apple Silicon, a terminal running under Rosetta is supported: the installer detects the hardware and selects native ARM64 Python. Intel Macs and musl distributions such as Alpine lack compatible wheels for this release. The default manager runs without Docker. Browser automation and optional sandbox integrations have separate setup requirements.

## Configure your provider locally

On the first `cuga start manager`, an interactive terminal guides you through provider selection before starting the manager.
Setup uses a dedicated terminal screen so keyboard navigation stays in place. Your previous terminal output returns when you leave setup.

1. Choose OpenAI, OpenRouter, watsonx, Ollama, Groq, Azure OpenAI, RITS, MiniMax, or an OpenAI-compatible private endpoint.
2. Enter a model identifier and endpoint URL. Credentials use hidden terminal input. For watsonx, also choose a project or space ID. Use arrow keys to select, **Enter** to continue, **Tab** to move between controls, and **Esc** or **Back** to revisit a step. Existing values are prefilled.
3. Review the connection and select **Test connection & save**. CUGA sends a short inference request and saves only after a successful test, in a local `.env` with permissions `0600`. If validation fails, your answers remain in the wizard: edit one field or retry without restarting. **Cancel** or **Ctrl+C** leaves the previous configuration unchanged.
4. When setup was started with `cuga start manager`, the manager starts after saving. When using `cuga setup`, the **Connection ready** screen shows your provider, model, and saved configuration path, with **Start manager** selected. You can also **Edit connection** or **Finish setup**; finishing prints the exact launch command for this installation and saved configuration. Open the printed URL and go to `/manage`. In **Configure & try it out**, ask **What can you help me automate?** Then connect tools and try a task using them.
5. Select **Publish** when your draft is ready. Published versions are used in **Chat**.

Existing provider configuration is detected and reused without prompts or additional test requests. To change or validate it explicitly:

```bash
cuga setup         # Change provider/model/credentials interactively
cuga setup --check # Test the existing connection without changing it
cuga start manager
```

Terminal setup uses the existing packaged model profiles, including `settings.watsonx.toml`, and writes `AGENT_SETTING_CONFIG`, `MODEL_NAME`, and provider environment variables. For watsonx, it recognizes both `WATSONX_API_KEY` and the existing `WATSONX_APIKEY` alias. It preserves unrelated `.env` keys and comments. Explicit `ENV_FILE` selects a file using CUGA's existing loading precedence; otherwise a project `.env` is used when found, with stored local configuration filling missing values. Shell values take precedence over implicit `.env` files.

For the local manager, the terminal connection remains authoritative even when saved agent configurations contain an older model or endpoint. Tools, policies, knowledge, and agent data remain in the manager. Repeated installation and normal restarts preserve them. Stop and restart the manager after changing the terminal configuration.

Docker presets, including the default CRM and knowledge agents in `Dockerfile.ubi`, keep their existing startup flow. They do not show a setup wizard or prompt for provider credentials. CI and other noninteractive launches do not prompt; configure the environment first, or use `ENV_FILE=/path/to/.env cuga start manager`.

## Local data

| Item | Default location |
| --- | --- |
| Provider configuration | `~/.local/share/cuga/.env` (credentials stored locally, permissions `0600`) |
| Agent configurations, policies, and encrypted manager secrets | `~/.local/share/cuga/dbs/cuga.db` |
| Encryption key | `~/.local/share/cuga/secret.key` (permissions `0600`) |
| Workspace | `~/.local/share/cuga/workspace/` |
| Logs | `~/.local/share/cuga/logs/` |
| Knowledge | `~/.local/share/cuga/knowledge/` |
| Isolated environment | Shown by `uv tool dir` |
| Command directory | Shown by `uv tool dir --bin` |

Set `CUGA_DATA_DIR` or `XDG_DATA_HOME` to select another data directory. Existing explicit storage, logging, and encryption-key settings take precedence. Provider loading follows the precedence described above. Back up the database **and encryption key together**; the key decrypts your saved credentials. Keep this directory private.

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
