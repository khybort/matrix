---
description: File a dev_agent task / accept / discard / revise / kill / pause / resume / lesson-approve
---

Dispatch a request to the Matrix dev_agent at http://agent.matrix.local. Use the Bash tool to run `curl`.

Parse the first argument as an optional sub-command. Supported sub-commands:

- (default — no sub-command, just description):
    Treat ALL arguments as the task description.
    Ask the user to confirm (showing defaults):
      - auto_commit (default: false)
      - auto_pr (default: false)
      - run_tests (default: true)
      - touches_files (default: [])
      - max_turns (default: 50)
    Then POST to /tasks. Return the new task_id and the worktree path
    once it's set (poll `GET /tasks/<id>` once after creation).

- `accept <id>`: POST /tasks/<id>/accept body `{"by":"user"}`
- `discard <id>`: POST /tasks/<id>/discard body `{"by":"user"}`
- `revise <id> "<notes>"`: POST /tasks/<id>/revise body `{"notes":"<notes>"}`
- `kill <id>`: POST /tasks/<id>/kill
- `pause "<reason>"`: POST /runtime/pause body `{"reason":"<reason>","by":"user"}`
- `resume`: POST /runtime/resume
- `lesson-approve <id>`: POST /lessons/<id>/approve body `{"by":"user"}`
- `queue`: GET /tasks?status=awaiting_review and pretty-print
- `status <id>`: GET /tasks/<id> and pretty-print

If a call returns connection refused or 5xx, tell the user to check `make dev-agent-tail`. Output the response and suggest the next likely `make dev-agent-*` command.
