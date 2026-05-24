# GitHub Token Debug Tool

Outil de debug pour générer des tokens GitHub App et tester l'intégration.

## 🎯 Objectif

Cet outil permet de :

- Générer un JWT signé avec votre clé privée GitHub App
- Échanger le JWT contre un token d'installation
- Tester l'authentification GitHub App
- Débugger les problèmes d'intégration

## 📋 Prérequis

1. **GitHub App configurée** (CNP-Portal, ID: 3836905)
2. **Clé privée** dans `backend/.env`
3. **Installation ID** de votre installation GitHub App

## 🚀 Usage

### Méthode 1 : Script Python (Recommandé)

```bash
cd backend

# Avec installation_id en argument
poetry run python debug_github_token.py 135177507

# Ou avec GITHUB_INSTALLATION_ID dans .env
poetry run python debug_github_token.py
```

**Note**: Le script utilise `asyncio` car `get_installation_token()` est une fonction asynchrone.

### Méthode 2 : Script Bash

```bash
cd backend

# Avec installation_id en argument
./debug_github_token.sh 12345678

# Ou avec GITHUB_INSTALLATION_ID dans .env
./debug_github_token.sh
```

## 🔍 Trouver votre Installation ID

1. Allez sur https://github.com/settings/installations
2. Cliquez sur "Configure" pour CNP-Portal
3. L'installation_id est dans l'URL :
   ```
   https://github.com/settings/installations/12345678
                                            ^^^^^^^^
   ```

## 📤 Output

Le script affiche :

```
============================================================
GitHub App Token Generator (Debug Tool)
============================================================

📋 Installation ID: 12345678

✅ GitHub service initialized
   App ID: 3836905

🔐 Generating JWT...
✅ JWT generated (expires in 10 minutes)
   JWT: eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...

🔑 Requesting installation token for installation 12345678...
✅ Installation token retrieved!

============================================================
TOKEN (valid for 1 hour):
============================================================
ghs_1234567890abcdefghijklmnopqrstuvwxyz
============================================================

💡 Usage examples:

  # List repositories
  curl -H "Authorization: Bearer ghs_..." \
       https://api.github.com/installation/repositories

  # Create a repository
  curl -X POST -H "Authorization: Bearer ghs_..." \
       -H "Content-Type: application/json" \
       -d '{"name":"test-repo","private":true}' \
       https://api.github.com/user/repos

  # Export as environment variable
  export GITHUB_TOKEN="ghs_..."
```

## 🧪 Tester le Token

### Lister les repos accessibles

```bash
TOKEN="ghs_..."
curl -H "Authorization: Bearer $TOKEN" \
     https://api.github.com/installation/repositories
```

### Créer un repo de test

```bash
TOKEN="ghs_..."
curl -X POST \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"name":"cnp-test-repo","private":true,"description":"Test repo from CNP"}' \
     https://api.github.com/user/repos
```

### Vérifier les permissions

```bash
TOKEN="ghs_..."
curl -H "Authorization: Bearer $TOKEN" \
     https://api.github.com/app/installations/12345678
```

## ⚙️ Configuration

Ajoutez dans `backend/.env` :

```bash
# GitHub App Configuration
GITHUB_APP_ID=3836905
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA...
-----END RSA PRIVATE KEY-----"

# Optional: Default installation ID for testing
GITHUB_INSTALLATION_ID=12345678
```

## 🐛 Troubleshooting

### Erreur : "GITHUB_APP_PRIVATE_KEY not configured"

**Solution** : Ajoutez votre clé privée dans `.env`

```bash
# Téléchargez la clé depuis GitHub
# Settings > Developer settings > GitHub Apps > CNP-Portal > Private keys

# Ajoutez-la dans .env (échappez les \n)
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----"
```

### Erreur : "Failed to get installation token"

**Causes possibles** :

- Installation ID incorrect
- GitHub App non installée sur le compte
- Clé privée invalide ou expirée
- Problème réseau

**Solution** :

1. Vérifiez l'installation ID sur https://github.com/settings/installations
2. Réinstallez la GitHub App si nécessaire
3. Régénérez la clé privée si elle est expirée

### Erreur : "Invalid JWT"

**Solution** : Vérifiez que :

- `GITHUB_APP_ID` est correct (3836905)
- La clé privée correspond bien à cette App
- L'horloge système est synchronisée (JWT expire après 10 min)

## 🔐 Sécurité

⚠️ **Important** :

- Les tokens générés sont valides **1 heure**
- Ne commitez JAMAIS les tokens dans Git
- Ne partagez pas les tokens publiquement
- Utilisez cet outil uniquement en développement/debug
- En production, les tokens sont générés automatiquement par le backend

## 📚 Ressources

- [GitHub Apps Documentation](https://docs.github.com/en/apps)
- [Authenticating with GitHub Apps](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app)
- [GitHub REST API](https://docs.github.com/en/rest)

## 🔗 Liens Utiles

- **GitHub App Settings** : https://github.com/settings/apps/cnp-portal
- **Installations** : https://github.com/settings/installations
- **API Docs** : https://docs.github.com/en/rest/apps/apps

## 💡 Exemples d'Utilisation

### Tester la création de repo

```bash
# 1. Générer le token
TOKEN=$(poetry run python debug_github_token.py 12345678 | grep "ghs_" | tail -1)

# 2. Créer un repo
curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "test-cnp-app",
    "description": "Test application from CNP",
    "private": true,
    "auto_init": true
  }' \
  https://api.github.com/user/repos

# 3. Vérifier
curl -H "Authorization: Bearer $TOKEN" \
     https://api.github.com/repos/YOUR_USERNAME/test-cnp-app
```

### Tester l'accès aux repos existants

```bash
TOKEN=$(poetry run python debug_github_token.py 12345678 | grep "ghs_" | tail -1)

# Lister tous les repos accessibles
curl -H "Authorization: Bearer $TOKEN" \
     https://api.github.com/installation/repositories | jq '.repositories[] | {name, private, url}'
```

### Débugger les permissions

```bash
TOKEN=$(poetry run python debug_github_token.py 12345678 | grep "ghs_" | tail -1)

# Voir les permissions de l'installation
curl -H "Authorization: Bearer $TOKEN" \
     https://api.github.com/app/installations/12345678 | jq '.permissions'
```
