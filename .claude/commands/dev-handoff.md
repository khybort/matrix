---
description: Hand off the current Claude Code session to the Matrix dev_agent
---

Build a handoff payload from the current conversation:

1. Summarize what we've been doing in this session in 3-5 sentences.
2. List up to 20 recent assistant messages (compact text only — no tool dumps).
3. List the files we've opened, edited, or referenced (best effort from session memory).
4. Append the user's free-text continuation instruction (the args to this slash command).

POST to http://agent.matrix.local/tasks with:
```json
{
  "description": "<continuation instruction>",
  "source": "handoff",
  "conversation_snapshot": {
    "summary": "<your 3-5 sentence summary>",
    "messages": ["<m1>", "<m2>", ...],
    "open_files": ["<path1>", ...]
  }
}
```

Return the new task_id. Tell the user they can close this session — the dev_agent will pick it up.
