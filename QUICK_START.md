# 🚀 Quick Start - Après les Correctifs

> **Tous les correctifs ont été appliqués avec succès !**
> Suivez ces étapes pour vérifier que tout fonctionne.

---

## ⚡ Démarrage Rapide (2 minutes)

### Étape 1 : Redémarrer le Backend

```bash
cd backend
poetry run uvicorn app.main:app --reload
```

**OU avec Docker :**

```bash
docker-compose down
docker-compose up -d --build
```

### Étape 2 : Tester le Profil

1. Ouvrez http://localhost:3000
2. Déconnectez-vous (si connecté)
3. Reconnectez-vous
4. Allez sur `/account`
5. ✅ Votre nom doit s'afficher correctement (pas "null null")

### Étape 3 : Nettoyer les Applications Bloquées (si nécessaire)

```bash
./backend/fix_stuck_deployments.sh
```

---

## 📋 Checklist Complète

### ✅ Backend

- [ ] Backend démarre sans erreur
- [ ] http://localhost:8000/health répond `{"status":"healthy"}`
- [ ] http://localhost:8000/api/account/me répond (avec token)

### ✅ Frontend

- [ ] Frontend démarre sans erreur
- [ ] http://localhost:3000 accessible
- [ ] Login Keycloak fonctionne
- [ ] Profil affiche votre nom

### ✅ Déploiements

- [ ] Catalogue accessible
- [ ] Séparation IaaS / PaaS visible
- [ ] Création d'un déploiement fonctionne
- [ ] Pas d'erreur "Unauthorized" (Kubernetes)
- [ ] Pas d'erreur "hashicorp/openstack" (IaaS)

---

## 🔧 Commandes Utiles

### Logs

```bash
# Backend logs
tail -f backend/logs/app.log

# Frontend logs (browser)
# F12 → Console tab

# Docker logs
docker logs -f arcl-backend
docker logs -f arcl-frontend

# Kubernetes logs
kubectl logs -n argocd -l app.kubernetes.io/name=argocd-server
```

### Base de Données

```bash
# Voir tous les déploiements
sqlite3 backend/arcl.db "SELECT id, name, status FROM deployments;"

# Nettoyer les déploiements bloqués
./backend/fix_stuck_deployments.sh

# Supprimer un déploiement spécifique
sqlite3 backend/arcl.db "DELETE FROM deployments WHERE id=X;"
```

### Kubernetes

```bash
# Vérifier la connexion
kubectl get nodes

# Voir les namespaces créés
kubectl get ns | grep project-

# Voir les applications ArgoCD
kubectl get applications -n argocd

# Voir les pods d'une application
kubectl get pods -n project-<nom>-<app>
```

---

## 🎯 Tests par Scénario

### Scénario 1 : Nouveau Déploiement IaaS (OpenStack + AWS)

```bash
1. Aller sur le catalogue
2. Onglet "Infrastructure as a Service (IaaS)"
3. Choisir un template (ex: "Web + DB")
4. Remplir le formulaire
5. Cliquer "Deploy"
6. Attendre que le statut passe à "running"
```

**Vérifications :**

- ✅ Pas d'erreur "hashicorp/openstack"
- ✅ Terraform crée les ressources
- ✅ Statut final : "running"

### Scénario 2 : Nouveau Déploiement PaaS (Kubernetes + GitOps)

```bash
1. Lier votre compte GitHub (si pas déjà fait)
   - Aller sur /account
   - Cliquer "Link GitHub Account"
   - Autoriser l'installation

2. Aller sur le catalogue
3. Onglet "Platform as a Service (PaaS)"
4. Choisir un template (ex: "FastAPI + React")
5. Remplir le formulaire (nom, projet, replicas)
6. Cliquer "Deploy"
7. Attendre que le statut passe à "running"
```

**Vérifications :**

- ✅ Pas d'erreur "Unauthorized"
- ✅ Repository GitHub créé
- ✅ Namespace Kubernetes créé
- ✅ Application ArgoCD créée
- ✅ Secrets Vault créés

```bash
# Vérifier
kubectl get ns | grep project-
kubectl get applications -n argocd
kubectl get pods -n project-<nom>-<app>
```

### Scénario 3 : Récupération après Crash

```bash
1. Si le backend a crashé pendant un déploiement
2. Des applications sont bloquées en "deploying" ou "deleting"

Solution :
./backend/fix_stuck_deployments.sh

3. Rafraîchir la page
4. Les applications sont maintenant "failed"
5. Vous pouvez les supprimer ou les relancer
```

---

## 📚 Documentation

Pour aller plus loin :

| Document                    | Description                   |
| --------------------------- | ----------------------------- |
| **CORRECTIFS_APPLIQUES.md** | ⭐ Résumé rapide en français  |
| **TROUBLESHOOTING.md**      | 🔧 Guide de dépannage complet |
| **README_FIXES.md**         | 📖 Documentation détaillée    |
| **FIXES_SUMMARY.md**        | 🧪 Tests techniques           |
| **CHANGES.txt**             | 📝 Liste des changements      |

---

## 🆘 En Cas de Problème

### Le profil affiche toujours "null null"

```bash
# 1. Vérifier que les changements sont appliqués
grep "computed_name" backend/app/routers/account.py
grep "given_name\|family_name" frontend/src/auth.ts

# 2. Redémarrer le backend
cd backend
poetry run uvicorn app.main:app --reload

# 3. Vider le cache du navigateur (Ctrl+Shift+Del)
# 4. Se déconnecter et reconnecter
```

### Erreur "Unauthorized" avec Kubernetes

```bash
# Si Docker
docker-compose down
docker-compose up -d --build

# Si local
export KUBECONFIG=~/.kube/config
cd backend
poetry run uvicorn app.main:app --reload

# Vérifier
kubectl get nodes
```

### Erreur "hashicorp/openstack"

```bash
# Vérifier que le changement est appliqué
grep "required_providers" backend/app/services/terraform_executor.py

# Redémarrer le backend
cd backend
poetry run uvicorn app.main:app --reload

# Le changement s'appliquera au prochain déploiement
```

### Applications bloquées

```bash
# Nettoyer avec le script
./backend/fix_stuck_deployments.sh

# OU manuellement
sqlite3 backend/arcl.db "UPDATE deployments SET status='failed' WHERE status IN ('deleting', 'deploying', 'planning', 'pending');"
```

---

## ✨ C'est Tout !

Si vous avez suivi ces étapes, votre plateforme est maintenant **100% opérationnelle** !

### Prochaines Étapes

1. ✅ Tester un déploiement IaaS complet
2. ✅ Tester un déploiement PaaS complet
3. 🚀 Continuer avec Phase 4 (Frontend amélioré)
4. 🚀 Préparer Phase 5 (Day-2 Operations)

---

**Bon développement ! 🎉**
