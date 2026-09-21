# Message pour Tester le Serveur MCP CNP dans Claude Desktop

Salut ! Tu peux copier-coller ce message dans Claude Desktop pour tester mon serveur MCP CNP :

---

## Message à envoyer à Claude :

Bonjour ! J'aimerais tester le serveur MCP CNP (Cloud Native Platform) qui est maintenant connecté.

Peux-tu me montrer ce que tu peux faire avec ce serveur ? Voici ce que j'aimerais essayer :

1. **Découvrir la documentation disponible :**
   - Peux-tu lister les ressources de documentation disponibles ?
   - Montre-moi la vue d'ensemble de l'architecture du système

2. **Explorer les outils API :**
   - Quels sont les outils (tools) disponibles pour interagir avec la plateforme ?
   - Explique-moi comment fonctionne le provisioning d'applications

3. **Test pratique (si possible) :**
   - Peux-tu me montrer comment tu pourrais lister les déploiements actifs ?
   - Comment créerais-tu une nouvelle application sur la plateforme ?

Note : Mon serveur MCP tourne en local, donc certaines opérations nécessitant des tokens d'authentification pourraient ne pas fonctionner complètement, mais tu devrais pouvoir accéder à la documentation et me montrer les capacités du serveur.

---

## Ce que tu devrais observer :

Si le serveur MCP fonctionne correctement, Claude devrait :

1. **Reconnaître le serveur CNP** et ses capacités
2. **Accéder à la documentation** (architecture, workflows, API specs, etc.)
3. **Lister les outils disponibles** :
   - `list_active_deployments` - Lister les déploiements
   - `list_projects` - Lister les projets
   - `deploy_new_app` - Créer une nouvelle application
   - `get_project_details` - Détails d'un projet
   - `check_deployment_status` - Vérifier le statut d'un déploiement

4. **Montrer des exemples** de comment utiliser ces outils

## Troubleshooting

Si ça ne fonctionne pas :

1. Vérifie que le serveur MCP est bien configuré dans Claude Desktop (`~/.config/Claude/claude_desktop_config.json` sur Linux)
2. Redémarre Claude Desktop après avoir modifié la config
3. Vérifie les logs du serveur dans Claude Desktop (Menu → Settings → Developer)

## Configuration attendue

Le fichier `claude_desktop_config.json` devrait contenir :

```json
{
  "mcpServers": {
    "cnp-portal": {
      "command": "poetry",
      "args": [
        "run",
        "python",
        "/home/brian/Documents/aepita/ing2/3-Istor/CMP/backend/app/mcp_server.py"
      ],
      "cwd": "/home/brian/Documents/aepita/ing2/3-Istor/CMP/backend",
      "env": {
        "PYTHONPATH": "/home/brian/Documents/aepita/ing2/3-Istor/CMP/backend"
      }
    }
  }
}
```

---

Bonne chance avec les tests ! 🚀
