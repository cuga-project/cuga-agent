# hello_tools

The smallest runnable CUGA agent: one `CugaAgent` and two plain Python tools (`convert_units` and `days_between`). Read `main.py` in two minutes, then run it.

## Two things that trip people up

- **The `@tool` line is required.** CUGA rejects plain functions with `ValueError: ... is not a valid LangChain tool`. Decorate each tool with `langchain_core.tools.tool`.
- **The docstring matters.** The model reads it to decide when to call each tool, so say clearly what the tool does and what its arguments mean.

## Check it works (no API key, no LLM)

```bash
cd docs/examples/hello_tools && uv run --project ../../../ python check.py
# OK: ['convert_units', 'days_between']
```

`check.py` tests both tools directly and confirms `CugaAgent` accepts them, using a fake model.

## Run the agent

With an API key: put `OPENAI_API_KEY=...` in the repo's `.env`, then:

```bash
cd docs/examples/hello_tools && uv run --project ../../../ python main.py
```

With free, local [Ollama](https://ollama.com): run `ollama pull gpt-oss:20b` (a large download), then:

```bash
AGENT_SETTING_CONFIG=settings.ollama.toml OPENAI_API_KEY=ollama uv run --project ../../../ python main.py
```

The setting comes from [`settings.ollama.toml`](../../../src/cuga/configurations/models/settings.ollama.toml). The answer should mention about 26.1 miles and 73 days.

## Try this next

Add a third tool of your own (for example, a word counter), pass it to `CugaAgent(tools=[...])`, and ask a question that needs it.
