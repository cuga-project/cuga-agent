Read the user's message and determine their planning preference.

Set `planning_preference` to exactly one of:
- `"human"` — if the user indicates they want a human to handle the planning (e.g. "by human", "human handling", "I'll plan it myself", "manual")
- `"agent"` — if the user wants automated or AI-based planning, or if no clear preference is expressed

Also extract the travel destination from the message if present and set `destination` accordingly.

Respond only by updating the process variables. Do not produce a narrative response.
