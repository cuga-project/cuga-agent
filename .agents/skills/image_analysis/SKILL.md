---
name: image_analysis
description: "Use this skill when the user wants to analyze, describe, or ask a question about an image. Supports image files in the workspace (e.g. /workspace/photo.jpg) and public image URLs. Triggers on: 'what is in this image', 'describe this photo', 'analyze this picture', 'what does this image show', or any request that requires visual understanding."
---

# Image Analysis Skill

Spawn ONE subagent with this exact prompt (fill in `<image>` and `<question>`):

```
Run this command and report its stdout verbatim. Do not spawn nested subagents. If it fails, report stderr and stop.

python ./skills/image_analysis/scripts/analyze_image.py --image "<image>" --question "<question>"
```

- `<image>` is the path or URL the user provided. For workspace files use `/workspace/<filename>`.
- Return the subagent output verbatim, then add a one-sentence summary.
