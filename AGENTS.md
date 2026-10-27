# Agent instructions (Claude Code, Codex, …)

Write everything that lands in this repo in English: code, comments, commits,
PR titles and descriptions.

## Jira: before, during, after

Work is tracked in Jira, project **CNP** on `3istors.atlassian.net`. Use the
Atlassian MCP server. If it is not connected, tell the human instead of skipping
these steps.

**Before coding**
- Find the ticket for the work: the one the human names, otherwise a JQL search
  on the keywords (`project = CNP AND text ~ "..."`).
- Read the ticket, its comments and its linked PRs. If someone already started,
  or another ticket touches the same files, say so before writing code.
- No ticket: offer to create one. Do not work outside a ticket.

**During**
- Put the key in the branch and the PR title: `feat/cnp-89-cloud-pricing`,
  `feat(finops): … (CNP-89)`. Commits stay in Conventional Commits.
- Comment on the ticket for each decision, finding that matters to others,
  blocker or opened PR. Short, factual, with links. What the team needs to know
  goes in Jira, not only in the conversation.

**After**
- PR merged or release cut: a summary comment (what shipped, the version, what
  is left) and the status updated.
- Remaining work becomes sub-tickets, not a list in a comment.

Never change the status, assignee or description of a ticket that is not yours
unless the human asks. Never delete anything in Jira.

## This repo

- Backend: `cd backend && poetry run pytest`, then `black --check app/`,
  `isort --check-only app/` and `pylint app/ --fail-under=9.0` (what CI runs).
  Frontend: `cd frontend && npm run lint && npm run build`.
- A release is a `vX.Y.Z` tag on `main`: CI builds the images, then
  argocd-image-updater deploys them. The backend runs with `Recreate` (SQLite on
  an RWO volume), so every release takes the API down for about a minute.
- The database is SQLite. A new Alembic migration chains onto the current head
  (`alembic heads` must keep a single head).
- Platform documentation lives in the `cnp-docs` repo.
