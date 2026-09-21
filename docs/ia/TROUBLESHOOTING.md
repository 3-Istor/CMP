# Guide de Dépannage CMP

Ce document liste les problèmes courants et leurs solutions.

---

## 1. Profil affichant "null null" ou "Welcome Developer"

### Symptômes

- L'interface affiche "Welcome Developer" au lieu de votre nom
- Le profil montre "null null"
- Les champs prénom/nom sont vides

### Cause

Les informations de prénom/nom ne sont pas correctement récupérées depuis Keycloak et propagées dans la session Next.js.

### Solution

**✅ Déjà corrigé dans ce commit !**

Les fichiers suivants ont été mis à jour :

- `backend/app/routers/account.py` - Récupération correcte depuis Keycloak
- `frontend/src/auth.ts` - Sauvegarde du nom dans la session

**Pour appliquer la correction :**

1. Redémarrez le backend :

```bash
cd backend
poetry run uvicorn app.main:app --reload
```

2. Déconnectez-vous et reconnectez-vous pour régénérer votre session

---

## 2. Erreur Terraform : "Unauthorized" avec Kubernetes

### Symptômes

```
Error: Unauthorized
│
│   with kubernetes_namespace.app_namespace,
│   on main.tf line XX, in resource "kubernetes_namespace" "app_namespace":
```

### Cause

Terraform n'a pas accès à votre cluster Kubernetes (K3s).

### Solutions

#### Option A : Backend local (avec `poetry run`)

Assurez-vous que kubectl fonctionne avant de lancer le backend :

```bash
# Test kubectl
kubectl get nodes

# Si ça échoue, configurez KUBECONFIG
export KUBECONFIG=~/.kube/config

# Ou pour K3s
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

# Puis lancez le backend
cd backend
poetry run uvicorn app.main:app --reload
```

#### Option B : Backend Docker (avec `docker-compose`)

**✅ Déjà corrigé dans ce commit !**

Le fichier `docker-compose.yml` a été mis à jour pour monter votre kubeconfig :

```yaml
volumes:
  - ~/.kube/config:/root/.kube/config:ro
```

Recréez le container :

```bash
docker-compose down
docker-compose up -d --build
```

#### Option C : Créer un ServiceAccount Kubernetes pour le CMP

Pour un environnement de production, créez un ServiceAccount dédié :

```bash
kubectl create serviceaccount cmp-terraform -n default
kubectl create clusterrolebinding cmp-terraform-admin \
  --clusterrole=cluster-admin \
  --serviceaccount=default:cmp-terraform

# Récupérer le token
kubectl create token cmp-terraform -n default --duration=8760h
```

Ajoutez le token dans `backend/.env` :

```bash
KUBE_TOKEN="eyJhbGc..."
KUBE_HOST="https://your-k3s-api:6443"
```

---

## 3. Applications bloquées en "Deploying" ou "Deleting"

### Symptômes

- Des applications restent figées dans l'état "deploying", "deleting", "pending" ou "planning"
- Impossible de les supprimer ou de les mettre à jour
- Elles persistent même après un redémarrage du backend

### Cause

Le backend a été interrompu (crash, redémarrage) pendant une opération Terraform, laissant la base de données dans un état incohérent.

### Solution

**✅ Script de nettoyage créé !**

Utilisez le script fourni :

```bash
# Depuis la racine du projet
./backend/fix_stuck_deployments.sh

# OU depuis le dossier backend
cd backend
./fix_stuck_deployments.sh
```

Le script va :

1. Afficher tous les déploiements bloqués
2. Vous demander confirmation
3. Les marquer comme "failed"
4. Vous permettre de les supprimer depuis l'interface

**Alternative manuelle :**

```bash
# Marquer tous les déploiements bloqués comme failed
sqlite3 backend/arcl.db "UPDATE deployments SET status='failed' WHERE status IN ('deleting', 'deploying', 'planning', 'pending');"

# Vérifier le résultat
sqlite3 backend/arcl.db "SELECT id, name, status FROM deployments;"
```

---

## 4. Erreur Terraform : "Provider hashicorp/openstack"

### Symptômes

```
Error: Failed to query available provider packages
│
│ Could not retrieve the list of available versions for provider hashicorp/openstack
```

### Cause

Le fichier `cmp_override.tf` généré ne spécifie pas le bon provider source.

### Solution

**✅ Déjà corrigé dans ce commit !**

Le fichier `backend/app/services/terraform_executor.py` a été mis à jour pour générer :

```hcl
terraform {
  required_providers {
    openstack = {
      source = "terraform-provider-openstack/openstack"
    }
  }
}
```

Redémarrez simplement le backend pour appliquer la correction.

---

## 5. Erreur GitHub App : "Bad credentials"

### Symptômes

```
GitHub App authentication failed: Bad credentials
```

### Causes possibles

1. La clé privée GitHub App n'est pas configurée
2. L'App ID est incorrect
3. L'installation ID est invalide

### Solutions

#### Vérifier la configuration

```bash
# backend/.env
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----
...
-----END RSA PRIVATE KEY-----"
```

**Note :** La clé doit inclure les lignes `-----BEGIN` et `-----END`.

#### Tester la génération de JWT

```bash
cd backend
poetry run python -c "
from app.services.github_service import generate_jwt
try:
    jwt_token = generate_jwt()
    print('✅ JWT généré avec succès')
    print(f'Token (premiers 50 chars): {jwt_token[:50]}...')
except Exception as e:
    print(f'❌ Erreur: {e}')
"
```

#### Vérifier l'installation ID

L'installation ID doit correspondre à une installation réelle de votre GitHub App.

Pour le trouver :

1. Allez sur https://github.com/settings/installations
2. Cliquez sur votre app
3. L'URL contient l'installation ID : `github.com/settings/installations/12345678`

---

## 6. Frontend : "Failed to load user profile"

### Symptômes

La page `/account` affiche une erreur au lieu du profil utilisateur.

### Diagnostic

Vérifiez les logs du backend :

```bash
# Si backend local
tail -f backend/logs/app.log

# Si Docker
docker logs -f arcl-backend
```

### Causes courantes

1. **Backend inaccessible**
   - Vérifiez que `http://localhost:8000/api/account/me` répond
   - Vérifiez la variable `NEXT_PUBLIC_API_URL` dans le frontend

2. **Token invalide**
   - Déconnectez-vous et reconnectez-vous
   - Vérifiez que Keycloak est accessible

3. **Keycloak inaccessible**
   - Vérifiez `KEYCLOAK_URL` dans `backend/.env`
   - Testez : `curl https://auth.3istor.com/realms/3istor`

---

## 7. CORS Errors dans le Frontend

### Symptômes

```
Access to fetch at 'http://localhost:8000/api/...' has been blocked by CORS policy
```

### Solution

Vérifiez la configuration CORS dans `backend/app/main.py` :

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # Ajoutez vos origines
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## 8. S3/Garage : Erreur d'upload d'avatar

### Symptômes

```
S3 upload failed: An error occurred (InvalidAccessKeyId) when calling the PutObject operation
```

### Solution

Vérifiez la configuration S3 dans `backend/.env` :

```bash
AVATARS_S3_ENDPOINT=https://s3.3istor.com
AVATARS_S3_ACCESS_KEY_ID=your-access-key
AVATARS_S3_SECRET_ACCESS_KEY=your-secret-key
AVATARS_S3_BUCKET=avatars
AVATARS_S3_REGION=garage
AVATARS_PUBLIC_URL_BASE=https://s3.3istor.com/avatars
```

Testez la connexion :

```bash
aws s3 ls s3://avatars \
  --endpoint-url https://s3.3istor.com \
  --profile your-profile
```

---

## Commandes Utiles

### Backend

```bash
# Logs en temps réel
tail -f backend/logs/app.log

# Vérifier la base de données
sqlite3 backend/arcl.db "SELECT * FROM deployments;"

# Migrations
cd backend
poetry run alembic upgrade head

# Tests
poetry run pytest
```

### Frontend

```bash
# Logs de build
npm run build

# Vérifier les variables d'environnement
npm run env

# Nettoyer le cache
rm -rf .next
npm run build
```

### Kubernetes

```bash
# Vérifier la connexion
kubectl get nodes

# Voir les namespaces créés par le CMP
kubectl get ns | grep project-

# Logs d'ArgoCD
kubectl logs -n argocd -l app.kubernetes.io/name=argocd-application-controller

# Voir les applications ArgoCD
kubectl get applications -n argocd
```

### Docker

```bash
# Reconstruire tout
docker-compose down
docker-compose build --no-cache
docker-compose up -d

# Logs
docker-compose logs -f backend
docker-compose logs -f frontend

# Nettoyer les volumes
docker-compose down -v
```

---

## Support

Si vous rencontrez un problème non documenté ici :

1. Vérifiez les logs du backend et frontend
2. Consultez les documentations dans `.kiro/steering/docs/`
3. Vérifiez que toutes les variables d'environnement sont définies
4. Essayez de reproduire le problème avec les logs en debug

### Activer le mode debug

Backend (`backend/.env`) :

```bash
LOG_LEVEL=DEBUG
```

Frontend :

```bash
NEXT_PUBLIC_LOG_LEVEL=debug
```
