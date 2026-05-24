# 🚀 Quick Start : Générer un Token GitHub

Guide rapide pour générer un token GitHub App en 2 minutes.

## ⚡ Usage Rapide

```bash
cd backend

# Méthode 1 : Python (recommandé)
poetry run python debug_github_token.py YOUR_INSTALLATION_ID

# Méthode 2 : Bash
./debug_github_token.sh YOUR_INSTALLATION_ID
```

## 🔍 Trouver votre Installation ID

1. **Allez sur** : https://github.com/settings/installations
2. **Cliquez** sur "Configure" pour **CNP-Portal**
3. **Copiez** l'ID dans l'URL :
   ```
   https://github.com/settings/installations/12345678
                                            ^^^^^^^^
                                            Votre ID
   ```

## ⚙️ Configuration (Une seule fois)

Ajoutez dans `backend/.env` :

```bash
# GitHub App ID (déjà configuré normalement)
GITHUB_APP_ID=3836905

# Votre clé privée (téléchargée depuis GitHub)
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA...
-----END RSA PRIVATE KEY-----"

# Optionnel : Installation ID par défaut
GITHUB_INSTALLATION_ID=12345678
```

## 📤 Exemple de Sortie

```
============================================================
GitHub App Token Generator (Debug Tool)
============================================================

📋 Installation ID: 12345678
📋 App ID: 3836905

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
```

## 🧪 Tester le Token

```bash
# Sauvegarder le token
TOKEN="ghs_..."

# Lister les repos
curl -H "Authorization: Bearer $TOKEN" \
     https://api.github.com/installation/repositories

# Créer un repo de test
curl -X POST \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"name":"test-repo","private":true}' \
     https://api.github.com/user/repos
```

## 🐛 Problèmes Courants

### "GITHUB_APP_PRIVATE_KEY not configured"

➡️ Ajoutez votre clé privée dans `backend/.env`

### "Failed to get installation token"

➡️ Vérifiez :

- L'installation ID est correct
- La GitHub App est bien installée
- La clé privée correspond à l'App

### "Invalid JWT"

➡️ Vérifiez :

- `GITHUB_APP_ID=3836905` dans `.env`
- La clé privée est correcte
- L'horloge système est synchronisée

## 📚 Documentation Complète

Pour plus de détails, voir : `backend/DEBUG_GITHUB_TOKEN.md`

## 💡 Astuce

Ajoutez `GITHUB_INSTALLATION_ID` dans `.env` pour ne pas avoir à le taper à chaque fois :

```bash
# Dans backend/.env
GITHUB_INSTALLATION_ID=12345678

# Puis simplement :
poetry run python debug_github_token.py
```
