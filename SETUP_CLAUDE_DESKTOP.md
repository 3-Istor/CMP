# 🤖 Test du Serveur MCP avec Claude Desktop

Salut ! J'ai intégré un serveur MCP (Model Context Protocol) dans mon projet CNP qui permet à Claude Desktop de lire la documentation du projet et d'interagir avec l'API. Voici comment le tester.

---

## 🎯 Ce que ça fait

Claude Desktop pourra :

- 📚 **Lire la documentation** du projet (architecture, workflows, API)
- 🔍 **Lister les déploiements** actifs
- 🚀 **Créer de nouveaux déploiements** Kubernetes
- 📊 **Vérifier le statut** des applications
- 🗑️ **Supprimer des déploiements**

Bref, Claude devient un assistant qui connaît **vraiment** le projet !

---

## ⚡ Setup Rapide (3 minutes)

### Étape 1 : Clone le projet

```bash
cd ~
git clone https://github.com/3-Istor/CMP.git
cd CMP/backend
```

### Étape 2 : Configure Claude Desktop

**Sur Linux/Mac :**

```bash
# Crée le dossier de config
mkdir -p ~/.config/Claude

# Crée le fichier de configuration
cat > ~/.config/Claude/claude_desktop_config.json << 'EOF'
{
  "mcpServers": {
    "cnp-portal": {
      "command": "poetry",
      "args": [
        "run",
        "python",
        "app/mcp_server.py"
      ],
      "cwd": "/CHANGE/THIS/PATH/TO/CMP/backend",
      "env": {
        "CMP_API_URL": "http://localhost:8000/api",
        "PYTHONUNBUFFERED": "1"
      }
    }
  }
}
EOF

# Édite le chemin 'cwd' avec le vrai chemin
nano ~/.config/Claude/claude_desktop_config.json
```

**Sur Windows :**
Crée le fichier `%APPDATA%\Claude\claude_desktop_config.json` avec :

```json
{
  "mcpServers": {
    "cnp-portal": {
      "command": "poetry",
      "args": ["run", "python", "app/mcp_server.py"],
      "cwd": "C:\\Users\\TON_USER\\CMP\\backend",
      "env": {
        "CMP_API_URL": "http://localhost:8000/api"
      }
    }
  }
}
```

**⚠️ IMPORTANT :** Change `cwd` avec le chemin absolu vers le dossier `backend/` !

### Étape 3 : Installe les dépendances

```bash
cd ~/CMP/backend
poetry install
```

### Étape 4 : Redémarre Claude Desktop

Ferme complètement Claude Desktop et relance-le.

**Tu devrais voir** : Un icône 🔌 en bas de l'interface = MCP connecté ! ✅

---

## 🧪 Tests à Faire

### Test 1 : Lire la Documentation

Demande à Claude :

```
Lis docs://index et dis-moi ce qu'est le projet CNP
```

**Résultat attendu :** Claude lit la vraie documentation et t'explique que c'est un Internal Developer Portal pour Kubernetes.

---

### Test 2 : Lire l'Architecture

```
Lis docs://01-architecture/01-system-overview et explique-moi l'architecture du système
```

**Résultat attendu :** Claude te décrit l'architecture GitOps avec ArgoCD, Keycloak, Vault, etc.

---

### Test 3 : Explorer la Roadmap

```
Lis docs://roadmap et dis-moi ce qui a été fait en Phase 3
```

**Résultat attendu :** Claude t'explique que Phase 3 a ajouté le support Kubernetes avec GitHub App integration.

---

### Test 4 : Documentation API

```
Lis docs://05-cmp-backend-api/01-cmp-deployment-api et explique-moi comment créer un déploiement
```

**Résultat attendu :** Claude te montre les endpoints API et la structure des requêtes.

---

### Test 5 : Recherche Spécifique

```
Trouve dans la documentation comment fonctionne l'authentification GitHub
```

**Résultat attendu :** Claude cherche dans docs://02-core-components/05-github-integration et t'explique le flow JWT.

---

## 🚀 Tests Avancés (Si backend démarré)

Si tu démarres le backend CNP :

```bash
cd ~/CMP/backend
poetry run uvicorn app.main:app --reload
```

Tu pourras tester les **outils API** (mais il faudra un token d'authentification) :

```
Liste tous les déploiements actifs
(token: eyJhbG...)
```

Claude appellera l'API et listera les déploiements ! 🎉

---

## 📚 Documentation Disponible

Claude a accès à **17 fichiers** de documentation :

```
01-architecture/
  • 01-system-overview.md
  • 02-tenancy-and-isolation.md

02-core-components/
  • 01-cmp-dashboard.md
  • 02-identity-keycloak.md
  • 03-secrets-vault.md
  • 04-gitops-argocd.md
  • 05-github-integration.md

03-pipelines-and-workflows/
  • 01-app-provisioning-flow.md
  • 02-ci-cd-pipelines.md
  • 03-developer-configuration-flow.md

04-templates/
  • 01-git-app-templates.md
  • 02-helm-generic-chart.md
  • 03-terraform-provisioner.md

05-cmp-backend-api/
  • 01-cmp-deployment-api.md
  • 02-cmp-phase3-changes.md
  • 03-cmp-frontend-integration.md
  • 11-cmp-mcp-integration.md
```

---

## 🐛 Dépannage

### Problème : Pas d'icône 🔌

**Vérifier la config :**

```bash
cat ~/.config/Claude/claude_desktop_config.json | jq .
```

Doit être du JSON valide avec le bon chemin `cwd`.

**Vérifier les logs Claude :**

```bash
tail -f ~/.config/Claude/logs/mcp*.log
```

---

### Problème : Claude ne répond pas

**Tester le serveur MCP manuellement :**

```bash
cd ~/CMP/backend
poetry run python app/mcp_server.py
```

Tu devrais voir :

```
🚀 Starting CNP Portal MCP Server
📚 Documentation path: .../docs
🔌 CMP API URL: http://localhost:8000/api

Available resources:
  - docs://index
  - docs://roadmap
  ...
```

Si ça plante, vérifie que Poetry et Python 3.12+ sont installés.

---

### Problème : "Documentation not found"

**Vérifier que la doc existe :**

```bash
ls -la ~/CMP/.kiro/steering/docs/
```

Tu devrais voir les dossiers `01-architecture/`, `02-core-components/`, etc.

---

## 💡 Exemples de Prompts Cool

Une fois que ça marche, essaie :

1. **Exploration** :

   ```
   Lis toute la documentation sur l'architecture et fais-moi un résumé
   ```

2. **Comparaison** :

   ```
   Compare le workflow legacy IaaS et le nouveau workflow Kubernetes
   ```

3. **Recherche** :

   ```
   Cherche comment les secrets sont gérés dans ce projet
   ```

4. **Explication** :

   ```
   Explique-moi le flow complet de création d'une application
   ```

5. **Technique** :
   ```
   Montre-moi comment l'intégration GitHub fonctionne avec JWT
   ```

---

## 🎉 Résultat Final

Si tout marche :

- ✅ Claude lit la documentation en temps réel
- ✅ Claude peut expliquer n'importe quelle partie du projet
- ✅ Claude peut répondre à des questions précises
- ✅ Claude peut comparer différentes sections
- ✅ Tu peux poser des questions naturelles sur le projet

C'est comme avoir un expert du projet disponible 24/7 ! 🤖

---

## 📞 Besoin d'Aide ?

**Fichiers utiles dans le projet :**

- `backend/QUICK_START_MCP.md` - Guide rapide
- `backend/MCP_SERVER_README.md` - Documentation complète
- `backend/test_mcp_manual.py` - Tests interactifs

**Logs à vérifier :**

```bash
# Logs Claude Desktop
tail -f ~/.config/Claude/logs/mcp*.log

# Logs du backend (si démarré)
tail -f ~/CMP/backend/logs/app.log
```

---

## 📊 Checklist de Test

Coche ce qui fonctionne :

- [ ] Claude Desktop installé
- [ ] Config MCP créée (`~/.config/Claude/claude_desktop_config.json`)
- [ ] Chemin `cwd` modifié avec le vrai chemin
- [ ] Dependencies installées (`poetry install`)
- [ ] Claude Desktop redémarré
- [ ] Icône 🔌 visible dans Claude
- [ ] Test 1 : `docs://index` → Claude répond ✅
- [ ] Test 2 : `docs://01-architecture/01-system-overview` → Claude répond ✅
- [ ] Test 3 : `docs://roadmap` → Claude répond ✅

Si tout est coché → **Ça marche parfaitement !** 🎉

---

**Bon test !** 🚀

Si tu bloques, envoie-moi :

1. Le contenu de ton fichier de config Claude
2. Les logs : `tail -f ~/.config/Claude/logs/mcp*.log`
3. Le résultat de : `poetry run python app/mcp_server.py`
