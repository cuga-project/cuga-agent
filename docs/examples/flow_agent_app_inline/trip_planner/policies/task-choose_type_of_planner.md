Read the user's message and determine their planning preference and destination.

Set the following output variables:
- `planning_preference`: exactly `"human"` if the user wants a human to handle the planning (e.g. "by human", "human handling", "I'll plan it myself", "manual"), or `"agent"` if they want automated/AI planning or no preference is expressed.
- `destination`: the travel destination extracted from the message, or an empty string if not mentioned.

Return a JSON object with exactly these two keys:
```json
{"planning_preference": "human", "destination": "Tokyo"}
```
