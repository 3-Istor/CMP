# Connecter un assistant IA à CNP

La CMP sert un serveur MCP distant sur `https://cmp.3istor.com/mcp`. Rien à
cloner ni à installer : l'assistant se connecte à la plateforme, l'utilisateur
se connecte avec son compte habituel et autorise l'assistant sur une page de
la CMP.

Le pas-à-pas par assistant (claude.ai, Claude Code, ChatGPT, Codex, OpenCode,
Cursor, VS Code) est sur https://cmp.3istor.com/connect-ai.

## Claude Code

```
/plugin marketplace add 3-Istor/cnp-agent-kit
/plugin install cnp@cnp-agent-kit
```

Le plugin configure la connexion et ajoute les guides de la plateforme. Sans
plugin : `claude mcp add --transport http cnp https://cmp.3istor.com/mcp`,
puis `/mcp` pour se connecter.

## Tester en local

1. Lancer la CMP en local (`make dev`). Le serveur MCP est sur
   `http://localhost:8000/mcp`.
2. Dans `backend/.env`, `CMP_PUBLIC_URL=http://localhost:3000` pour que la
   page d'autorisation s'ouvre sur le frontend local.
3. `claude mcp add --transport http cnp-local http://localhost:8000/mcp`,
   puis `/mcp`, ou MCP Inspector (`npx @modelcontextprotocol/inspector`)
   sur la même URL.

## Où est le code

| Quoi | Où |
|---|---|
| Outils, ressources, prompts | `backend/app/mcp_server.py` |
| OAuth porté par la CMP | `backend/app/services/agents/provider.py`, `store.py` |
| Garde-fous par niveau d'enjeu | `backend/app/services/agents/policy.yaml`, `policy.py` |
| Pages consentement, confirmation, connexion | `frontend/src/app/connect/`, `frontend/src/app/connect-ai/` |
| Guides (skills) | dépôt `3-Istor/cnp-agent-kit` |
