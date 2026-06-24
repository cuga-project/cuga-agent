# Reasoning Modes & Task Modes

[← Back to README](../README.md)

## Reasoning modes - Switch between Fast/Balanced/Accurate modes

### Available Modes under `./src/cuga`

| Mode       | File                                   | Description                                     |
| ---------- | --------------------------------------- | ----------------------------------------------- |
| `fast`     | `./configurations/modes/fast.toml`     | Optimized for speed                             |
| `balanced` | `./configurations/modes/balanced.toml` | Balance between speed and precision _(default)_ |
| `accurate` | `./configurations/modes/accurate.toml` | Optimized for precision                         |
| `custom`   | `./configurations/modes/custom.toml`   | User-defined settings                           |

### Configuration

```
configurations/
├── modes/fast.toml
├── modes/balanced.toml
├── modes/accurate.toml
└── modes/custom.toml
```

Edit `settings.toml`:

```toml
[features]
cuga_mode = "fast"  # or "balanced" or "accurate" or "custom"
```

**Documentation:** [./flags.html](./flags.html)

## Task Mode Configuration - Switch between API/Web/Hybrid modes

### Available Task Modes

| Mode     | Description                                                                 |
| -------- | ----------------------------------------------------------------------------- |
| `api`    | API-only mode - executes API tasks _(default)_                              |
| `web`    | Web-only mode - executes web tasks using browser extension                  |
| `hybrid` | Hybrid mode - executes both API tasks and web tasks using browser extension |

### How Task Modes Work

#### API Mode (`mode = 'api'`)

- Opens tasks in a regular web browser
- Best for API/Tools-focused workflows and testing

#### Web Mode (`mode = 'web'`)

- Interface inside a browser extension (available next to browser)
- Optimized for web-specific tasks and interactions
- Direct access to web page content and controls

#### Hybrid Mode (`mode = 'hybrid'`)

- Opens inside browser extension like web mode
- Can execute both API/Tools tasks and web page tasks simultaneously
- Starts from configurable URL defined in `demo_mode.start_url`
- Most versatile mode for complex workflows combining web and API operations

### Configuration

Edit `./src/cuga/settings.toml`:

```toml
[demo_mode]
start_url = "https://opensource-demo.orangehrmlive.com/web/index.php/auth/login"  # Starting URL for hybrid mode


[advanced_features]
mode = 'api'  # 'api', 'web', or 'hybrid'
```
