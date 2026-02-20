from collections import defaultdict

# Prompts used while storing run steps. Kept as a lightweight hook for agent-specific
# custom summarization prompts in CUGA.
DEFAULT_PROMPT = "Summarize the step in one sentence and return JSON with a required `summary` field."

prompts = defaultdict(lambda: DEFAULT_PROMPT)
prompts.update(
    {
        # Add custom node prompts here when needed.
    }
)
