# LLM Provider Configuration

[← Back to README](../README.md)

Refer to: [`.env.example`](../.env.example) for detailed examples.

CUGA supports multiple LLM providers with flexible configuration options. You can configure models through TOML files or override specific settings using environment variables.

## Supported Platforms

- **OpenAI** - GPT models via OpenAI API (also supports LiteLLM via base URL override)
- **IBM WatsonX** - IBM's enterprise LLM platform
- **Azure OpenAI** - Microsoft's Azure OpenAI service
- **Groq** - High-performance inference platform with fast LLM models
- **RITS** - Internal IBM research platform
- **OpenRouter** - LLM API gateway provider

## Configuration Priority

1. **Environment Variables** (highest priority)
2. **TOML Configuration** (medium priority)
3. **Default Values** (lowest priority)

### Option 1: OpenAI

**Setup Instructions:**

1. Create an account at [platform.openai.com](https://platform.openai.com)
2. Generate an API key from your [API keys page](https://platform.openai.com/api-keys)
3. Add to your `.env` file:
   ```env
   # OpenAI Configuration
   OPENAI_API_KEY=sk-...your-key-here...
   AGENT_SETTING_CONFIG="settings.openai.toml"

   # Optional overrides
   MODEL_NAME=gpt-4o                    # Override model name
   OPENAI_BASE_URL=https://api.openai.com/v1  # Override base URL
   OPENAI_API_VERSION=2024-08-06        # Override API version
   ```

**Default Values:**

- Model: `gpt-4o`
- API Version: OpenAI's default API Version
- Base URL: OpenAI's default endpoint

### Option 2: IBM WatsonX

**Setup Instructions:**

1. Access [IBM WatsonX](https://www.ibm.com/watsonx)
2. Create a project or space and get your credentials:
   - Project ID or Space ID
   - API Key
   - Region/URL
3. Add to your `.env` file:

   ```env
   # WatsonX Configuration
   WATSONX_API_KEY=your-watsonx-api-key
   WATSONX_PROJECT_ID=your-project-id
   # WATSONX_SPACE_ID=your-space-id  # Alternative to WATSONX_PROJECT_ID
   WATSONX_URL=https://us-south.ml.cloud.ibm.com  # or your region
   AGENT_SETTING_CONFIG="settings.watsonx.toml"

   # Optional override
   MODEL_NAME=meta-llama/llama-4-maverick-17b-128e-instruct-fp8  # Override model for all agents
   ```

**Default Values:**

- Model: `meta-llama/llama-4-maverick-17b-128e-instruct-fp8`

### Option 3: Azure OpenAI

**Setup Instructions:**

1. Add to your `.env` file:
   ```env
    AGENT_SETTING_CONFIG="settings.azure.toml"  # Default config uses ETE
    AZURE_OPENAI_API_KEY="<your azure apikey>"
    AZURE_OPENAI_ENDPOINT="<your azure endpoint>"
    OPENAI_API_VERSION="2024-08-01-preview"
   ```

### Option 4: LiteLLM Support

CUGA supports LiteLLM through the OpenAI configuration by overriding the base URL:

1. Add to your `.env` file:

   ```env
   # LiteLLM Configuration (using OpenAI settings)
   OPENAI_API_KEY=your-api-key
   AGENT_SETTING_CONFIG="settings.openai.toml"

   # Override for LiteLLM
   MODEL_NAME=Azure/gpt-4o              # Override model name
   OPENAI_BASE_URL=https://your-litellm-endpoint.com  # Override base URL
   OPENAI_API_VERSION=2024-08-06        # Override API version
   ```
### Option 5: Groq Support

**Setup Instructions:**

1. Create an account at [groq.com](https://groq.com)
2. Generate an API key from your [API keys page](https://console.groq.com/keys)
3. Add to your `.env` file:
   ```env
   # Groq Configuration
   GROQ_API_KEY=your-groq-api-key-here
   AGENT_SETTING_CONFIG="settings.groq.toml"

   # Optional override
   MODEL_NAME=llama-3.1-70b-versatile  # Override model name
   ```

**Default Values:**

- Model: Configured in `settings.groq.toml`
- Base URL: Groq's default endpoint

### Option 6: OpenRouter Support

**Setup Instructions:**

1. Create an account at [openrouter.ai](https://openrouter.ai)
2. Generate an API key from your account settings
3. Add to your `.env` file:
   ```env
   # OpenRouter Configuration
   OPENROUTER_API_KEY=your-openrouter-api-key
   AGENT_SETTING_CONFIG="settings.openrouter.toml"
   OPENROUTER_BASE_URL="https://openrouter.ai/api/v1"
    # Optional override
   MODEL_NAME=openai/gpt-4o                    # Override model name
    ```

## Configuration Files

CUGA uses TOML configuration files located in `src/cuga/configurations/models/`:

- `settings.openai.toml` - OpenAI configuration (also supports LiteLLM via base URL override)
- `settings.watsonx.toml` - WatsonX configuration
- `settings.azure.toml` - Azure OpenAI configuration
- `settings.groq.toml` - Groq configuration
- `settings.openrouter.toml` - OpenRouter configuration

Each file contains agent-specific model settings that can be overridden by environment variables.
