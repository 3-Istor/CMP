# Configuration du MCP Server CNP pour Claude Desktop 🚀

Salut ! Voici comment configurer le serveur MCP (Model Context Protocol) de la Cloud Native Platform dans ton Claude Desktop.

## C'est quoi ?

Un serveur MCP qui permet à Claude d'interagir directement avec la plateforme CNP :

- 📚 Lire la documentation technique complète
- 🔍 Consulter l'architecture, les workflows, les APIs
- 🛠️ Créer et gérer des déploiements (si token fourni)

## Installation Rapide (5 minutes)

### Étape 1 : Localiser le fichier de configuration

**Sur macOS** :

```bash
open ~/Library/Application\ Support/Claude/
```

**Sur Windows** :

```
%APPDATA%\Claude\
```

Tu dois trouver ou créer le fichier `claude_desktop_config.json`

### Étape 2 : Ajouter la configuration

Ouvre `claude_desktop_config.json` et ajoute cette configuration :

```json
{
  "mcpServers": {
    "cnp-portal": {
      "command": "python",
      "args": [
        "/home/brian/Documents/aepita/ing2/3-Istor/CMP/backend/app/mcp_server.py"
      ],
      "env": {
        "PYTHONPATH": "/home/brian/Documents/aepita/ing2/3-Istor/CMP/backend",
        "CMP_API_URL": "http://localhost:8000/api"
      }
    }
  }
}
```

⚠️ **IMPORTANT** : Remplace `/home/brian/Documents/aepita/ing2/3-Istor/CMP/backend` par le chemin complet vers le dossier `backend` sur ta machine.

### Étape 3 : Redémarrer Claude Desktop

Quitte complètement Claude Desktop et relance-le.

## Comment tester que ça fonctionne

### Test 1 : Vérifier la connexion

Dans Claude Desktop, pose cette question :

```
Peux-tu lire la documentation index du CNP ?
```

Claude devrait pouvoir accéder à la doc et te résumer le projet.

### Test 2 : Explorer l'architecture

```
Lis la documentation sur l'architecture système du CNP (01-architecture/01-system-overview)
et explique-moi comment fonctionne le provisioning.
```

### Test 3 : Consulter les APIs

```
Lis la spec de l'API de déploiement (05-cmp-backend-api/01-cmp-deployment-api)
et montre-moi comment créer un nouveau déploiement Kubernetes.
```

## Ressources Disponibles

### 📚 Documentation

Claude peut accéder à toute la doc technique :

- **`docs://index`** - Vue d'ensemble du projet
- **`docs://roadmap`** - Roadmap d'implémentation
- **`docs://01-architecture/01-system-overview`** - Architecture globale
- **`docs://02-core-components/01-cmp-dashboard`** - Dashboard CMP
- **`docs://03-pipelines-and-workflows/01-app-provisioning-flow`** - Workflows
- **`docs://04-templates/01-git-app-templates`** - Templates Git
- **`docs://05-cmp-backend-api/01-cmp-deployment-api`** - API Deployments

Et plus encore ! Liste complète dans `.kiro/steering/docs/`

### 🛠️ Outils API (optionnel, nécessite token)

Si tu veux que Claude puisse créer des déploiements :

1. Démarre le backend CMP :

```bash
cd /home/brian/Documents/aepita/ing2/3-Istor/CMP/backend
poetry run uvicorn app.main:app --reload
```

2. Récupère un token Keycloak valide

3. Demande à Claude :

```
Liste-moi les déploiements actifs (utilise le token: eyJ...)
```

## Exemples d'utilisation

### Exemple 1 : Comprendre l'architecture

```
Question : "Explique-moi comment fonctionne le multi-tenancy dans CNP"

Claude va :
1. Lire docs://02-tenancy-and-isolation
2. Résumer les 5 dimensions d'isolation
3. Expliquer les policies Cilium, Vault, etc.
```

### Exemple 2 : Apprendre les APIs

```
Question : "Montre-moi comment créer un déploiement Kubernetes via l'API"

Claude va :
1. Lire docs://05-cmp-backend-api/01-cmp-deployment-api
2. Te montrer la structure JSON exacte
3. Expliquer les champs requis
```

### Exemple 3 : Déboguer un problème

```
Question : "J'ai une erreur lors du bootstrapping Terraform,
que dois-je vérifier ?"

Claude va :
1. Lire docs://04-templates/03-terraform-provisioner
2. Consulter docs://03-pipelines-and-workflows/01-app-provisioning-flow
3. Te guider sur le diagnostic
```

## Dépannage

### Problème : "Server not responding"

**Solution** :

1. Vérifie que Python est bien dans ton PATH
2. Vérifie le chemin absolu dans `claude_desktop_config.json`
3. Redémarre Claude Desktop complètement

### Problème : "Documentation not found"

**Solution** :

1. Vérifie que le dossier `.kiro/steering/docs/` existe
2. Vérifie la variable `PYTHONPATH` dans la config

### Problème : "API tools not working"

**Solution** :
C'est normal ! Les outils API nécessitent :

- Le backend CMP en cours d'exécution
- Un token Keycloak valide

Les ressources de documentation fonctionnent toujours sans backend.

## Structure de la Documentation

Voici ce que Claude peut lire :

```
docs/
├── index.md                     # Vue d'ensemble
├── roadmap.md                   # Feuille de route
├── 01-architecture/             # Architecture système
├── 02-core-components/          # Composants (Keycloak, Vault, ArgoCD)
├── 03-pipelines-and-workflows/  # Workflows de provisioning
├── 04-templates/                # Templates et Terraform
└── 05-cmp-backend-api/          # Spécifications API
```

## Pour aller plus loin

### Lire toute une catégorie

```
Liste-moi tous les fichiers de documentation disponibles
et résume ce qu'il y a dans la catégorie "02-core-components"
```

### Comparer deux approches

```
Compare le flow de provisioning legacy (IaaS)
avec le flow Kubernetes (GitOps) en lisant les docs appropriées
```

### Générer du code

```
En te basant sur la spec API de déploiement,
génère-moi une fonction TypeScript pour créer un nouveau déploiement
```

## Support

Si tu as des questions ou des problèmes :

1. Vérifie que le chemin dans `claude_desktop_config.json` est correct
2. Regarde les logs de Claude Desktop (Help > Show Logs)
3. Essaie de relancer complètement Claude Desktop

## Checklist de vérification

- [ ] Fichier `claude_desktop_config.json` créé/modifié
- [ ] Chemin vers `mcp_server.py` correct (absolu)
- [ ] Claude Desktop redémarré complètement
- [ ] Test "Lis docs://index" fonctionne
- [ ] Claude peut accéder à différentes sections de doc

## C'est tout ! 🎉

Une fois configuré, tu peux demander à Claude n'importe quoi sur :

- L'architecture CNP
- Les workflows de déploiement
- Les spécifications API
- Les templates Terraform
- La configuration Keycloak/Vault/ArgoCD
- Les stratégies de multi-tenancy
- Et bien plus !

Claude aura accès à toute la documentation technique en temps réel.

---

**Note** : Les outils API (créer des déploiements, etc.) nécessitent le backend en cours d'exécution et un token valide. La documentation fonctionne toujours, même sans backend.
