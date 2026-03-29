# ☁️ CMP - Version Simple

## 📋 Prérequis

```bash
python3 --version  # Python 3.8+
```

## 🚀 Démarrage (2 minutes)

### 1. Installer

```bash
pip install -r requirements.txt
chmod +x deploy.sh
```

### 2. Backend (Terminal 1)

```bash
python3.10 backend.py
```

Le backend démarre sur: **http://localhost:8000**

### 3. Frontend (Terminal 2)

```bash
python3 -m http.server 3000
```

Ouvrez dans le navigateur: **http://localhost:3000/index_simple.html**

## 📁 Fichiers

- main.py
- index.html
- deploy.sh                 ← Script de déploiement
- apps.json                 ← Configs des apps
- requirements_simple.txt   ← Dépendances

## ⚙️ Configuration

Éditer `apps.json` pour ajouter vos applications:

```json
{
    "votre-app": {
        "ip": "10.0.1.100",
        "port": 8000
    }
}
```

## 🔧 Le script `deploy.sh`

Actuellement il:
1. Lance terraform (ou crée un mock si pas de fichiers)
2. Lance ansible (ou crée un mock si pas de playbook)

Pour utiliser vos vrais scripts:
1. Créer: `terraform/staging/main.tf` et `terraform/production/main.tf`
2. Créer: `ansible/deploy_app-name.yml`
3. Créer: `ansible/inventory`

## 📖 API Endpoints

```bash
# Déployer
curl -X POST "http://localhost:8000/deploy?app_name=web-app&env=staging"

# Vérifier le statut
curl "http://localhost:8000/status/web-app"

# Lister les apps
curl "http://localhost:8000/apps"
```
