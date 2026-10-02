# Hello, tools

This is the smallest runnable CUGA agent example: one `CugaAgent` with two plain Python tools.

## What it shows

- `@tool` is required. CUGA rejects plain functions that are not valid LangChain tools.
- Tool docstrings matter because the model reads them to decide when a tool should be used.
- `convert_units` converts common units.
- `days_between` calculates the number of days between two ISO dates.

## Keyless smoke check

From this directory:

```bash
uv run --project ../../../ python check.py
```

The check does not call an LLM. It verifies the tool results and confirms that `CugaAgent` accepts both tools.

Expected output:

```text
OK: ['convert_units', 'days_between']
```

## Run the agent

With an OpenAI-compatible key, put `OPENAI_API_KEY=...` in the repository `.env`, then run:

```bash
uv run --project ../../../ python main.py
```

For free local execution with Ollama, install Ollama, run `ollama pull gpt-oss:20b`, then:

```bash
AGENT_SETTING_CONFIG=settings.ollama.toml OPENAI_API_KEY=ollama uv run --project ../../../ python main.py
```

The agent should report about 26.1 miles for 42 km and 73 days between the example dates.

## Try this next

Add a third `@tool` of your own and include a clear docstring describing when the agent should use it.
