# 🎬 Script de Démo MCP pour Claude Desktop

Ce fichier montre exactement ce qui se passe quand tu testes avec Claude.

---

## 🎯 Démo 1 : Lecture de Documentation

### Toi → Claude :

```
Lis docs://index et dis-moi ce qu'est le projet CNP
```

### Claude → Toi :

```
📚 Je lis le document...

[MCP appelle: get_docs_index()]

D'après la documentation, le Cloud Native Platform (CNP) est un
**Internal Developer Portal (IDP)** moderne conçu pour abstraire
complètement la complexité de l'infrastructure.

Voici ce que j'ai trouvé :

🎯 Objectifs Principaux :
• Autonomie des développeurs : Provisionnement self-service
• Multi-tenancy strict : Isolation réseau, secrets, RBAC
• Automatisation end-to-end : GitHub → Kubernetes automatique

🏗️ Architecture :
• Frontend : Next.js 15 + Tailwind + Shadcn UI
• Backend : FastAPI + SQLAlchemy
• Infrastructure : Kubernetes (K3s) + ArgoCD + Terraform
• Sécurité : Keycloak SSO + Vault + Cilium

📦 Fonctionnalités :
1. Catalogue de templates (FastAPI, React, etc.)
2. Provisionnement automatique (GitHub repo + K8s namespace)
3. GitOps natif (ArgoCD synchronise tout)
4. Gestion des secrets (Vault Operator)
5. SSO Edge (Envoy Gateway + Keycloak)

Le projet est en Phase 3 (support Kubernetes complet) et
Phase 4 (frontend) est en cours.
```

---

## 🎯 Démo 2 : Exploration Architecture

### Toi → Claude :

```
Lis docs://01-architecture/01-system-overview et explique comment les
développeurs déploient une application
```

### Claude → Toi :

```
📚 Je lis l'architecture...

[MCP appelle: get_documentation("01-architecture", "01-system-overview")]

Voici le flow complet de déploiement :

🔄 Le Flow "Code-to-Cluster" :

1️⃣ DÉVELOPPEUR (Frontend)
   • Visite le CMP Dashboard
   • Sélectionne un template (ex: "FastAPI + React")
   • Remplit le formulaire (nom app, projet, replicas)
   • Clique "Deploy"

2️⃣ CMP BACKEND (Orchestrateur)
   • Génère un token GitHub App temporaire
   • Lance Terraform en arrière-plan
   • Suit la progression

3️⃣ TERRAFORM (Bootstrapping Day-0)
   • Crée le repo GitHub privé
   • Push le code template
   • Crée le namespace Kubernetes
   • Génère les secrets dans Vault
   • Enregistre l'app dans ArgoCD

4️⃣ ARGOCD (GitOps)
   • Détecte la nouvelle Application CRD
   • Clone le repo GitHub
   • Applique le Helm chart générique
   • Déploie dans Kubernetes

5️⃣ KUBERNETES (Runtime)
   • Pods démarrés
   • Vault Secrets injectés
   • Ingress configuré (Envoy Gateway)
   • SSO optionnel activé (Keycloak)

🎯 Avantage Principal :
Le développeur n'a JAMAIS à toucher Kubernetes directement !
Tout est géré par GitOps. Pour scaler, il édite juste un YAML
dans son repo GitHub, et ArgoCD applique automatiquement.

Architecture découplée : CMP → Terraform → Git → ArgoCD → K8s
```

---

## 🎯 Démo 3 : Recherche Spécifique

### Toi → Claude :

```
Comment fonctionne l'authentification GitHub dans ce projet ?
```

### Claude → Toi :

```
🔍 Je cherche dans la documentation...

[MCP appelle: get_documentation("02-core-components", "05-github-integration")]

Excellent question ! Voici comment ça marche :

🔐 Modèle GitHub App (pas de PAT)

Le projet utilise une **GitHub App** dédiée au lieu de tokens
personnels, pour la sécurité :

1️⃣ CRÉATION (One-time setup)
   • GitHub App créée : "CNP-Portal" (ID: 3836905)
   • Permissions : Repository (read/write), Administration
   • Clé privée RSA téléchargée

2️⃣ ONBOARDING UTILISATEUR
   • L'admin clique "Link GitHub" dans le CMP
   • Redirigé vers GitHub : /apps/cnp-portal/installations/new
   • Sélectionne l'org/compte à lier
   • GitHub renvoie un `installation_id`
   • CMP stocke cet ID dans la DB du projet

3️⃣ GÉNÉRATION TOKEN (À chaque déploiement)
   a) CMP génère un JWT signé avec la clé privée
      • Header: {"alg": "RS256", "typ": "JWT"}
      • Payload: {"iss": 3836905, "exp": now+10min}
      • Signature: RSA avec la clé privée

   b) CMP envoie le JWT à GitHub :
      POST /app/installations/{installation_id}/access_tokens

   c) GitHub retourne un token d'installation (1h)

   d) CMP passe ce token à Terraform

4️⃣ TERRAFORM UTILISE LE TOKEN
   • Crée le repo privé
   • Push le code
   • Configure branch protection

🔒 Sécurité :
• JWT : 10 minutes de validité
• Installation Token : 1 heure
• Jamais stocké en DB
• Régénéré à chaque opération
• Scope limité à l'installation

🎯 Avantage vs PAT :
• Pas de token global
• Permissions granulaires
• Auto-rotation
• Multi-org support
```

---

## 🎯 Démo 4 : Comparaison de Workflows

### Toi → Claude :

```
Lis la roadmap et explique la différence entre Phase 2 et Phase 3
```

### Claude → Toi :

```
📚 Je lis la roadmap...

[MCP appelle: get_roadmap()]

Voici l'évolution majeure :

📦 PHASE 2 (Terraform Migration) - Avril 2026
════════════════════════════════════════════

❌ Avant : SDK direct (boto3, openstacksdk)
✅ Après : Terraform pour tout

Changements :
• Orchestrateur SAGA refactoré
• State S3 + DynamoDB locking
• Outputs structurés
• Mais encore LEGACY (VMs uniquement)

Résultat : Infrastructure as Code, mais encore des VMs


🚀 PHASE 3 (Kubernetes) - Mai 2026 ✅ COMPLET
════════════════════════════════════════════

🎯 LE GROS CHANGEMENT : Multi-Provider !

Nouveau :
• Support Kubernetes (GitOps natif)
• GitHub App integration (JWT flow)
• ArgoCD provisioning automatique
• Terraform bootstrap module
• Micro-state pattern (état par app)

Architecture :
• Legacy Hybrid : OpenStack + AWS ASG (préservé)
• Kubernetes : GitHub + ArgoCD + Helm (nouveau)

Database :
• Nouveau champ : `provider_type` enum
• 4 nouveaux champs : github_repo_url, argocd_app_name, etc.

Impact Frontend :
• Dual catalog (IaaS vs PaaS)
• Nouveaux endpoints conditionnels
• GitHub account linking

🔥 Différence Clé :

Phase 2 → IaC (Terraform) mais encore VMs
Phase 3 → PaaS natif Kubernetes + GitOps complet

En Phase 3, un développeur ne touche JAMAIS Kubernetes !
Il push du code, ArgoCD synchronise tout automatiquement.
C'est une vraie plateforme "Heroku-like" interne.


📋 PHASE 4 (Frontend) - En cours
═════════════════════════════════

Prochaine étape :
• UI pour les deux providers
• GitHub linking visuel
• ArgoCD health widgets
• Day-2 ops (scale, rollback)
```

---

## 🎯 Démo 5 : Question Multi-Documents

### Toi → Claude :

```
Explique-moi le cycle de vie complet d'une application, de la création
à la destruction, en lisant toute la doc nécessaire
```

### Claude → Toi :

```
📚 Je vais lire plusieurs documents...

[MCP appelle: get_documentation("03-pipelines-and-workflows", "01-app-provisioning-flow")]
[MCP appelle: get_documentation("03-pipelines-and-workflows", "02-ci-cd-pipelines")]
[MCP appelle: get_documentation("03-pipelines-and-workflows", "03-developer-configuration-flow")]

Voici le cycle de vie COMPLET :

═══════════════════════════════════════════════════════════
📅 DAY-0 : PROVISIONING (Terraform + GitHub + ArgoCD)
═══════════════════════════════════════════════════════════

1. DÉCLENCHEUR
   Developer → CMP UI → "Deploy" button

2. GITHUB BOOTSTRAP
   ├─ Génération JWT (10 min)
   ├─ Token installation (1h)
   ├─ Création repo privé
   ├─ Push code template
   └─ Branch protection "main"

3. KUBERNETES SETUP
   ├─ Namespace créé : `project-alpha-app-front`
   ├─ Labels : project=alpha, app=front
   └─ NetworkPolicy : Default deny

4. SECRETS MANAGEMENT
   ├─ Path Vault : `kvv2/projects/alpha/front/`
   ├─ Génération passwords (32 chars)
   ├─ Vault Role : K8s ServiceAccount bound
   └─ VaultSecret CRD déployé

5. ARGOCD REGISTRATION
   ├─ Repository Secret (GitHub App creds)
   ├─ Application CRD créé
   ├─ AppProject : project-alpha
   └─ RBAC : group-based permissions

Status CMP : RUNNING → Developer notified


═══════════════════════════════════════════════════════════
📅 DAY-1 : CI/CD (GitHub Actions + ArgoCD Image Updater)
═══════════════════════════════════════════════════════════

6. DEVELOPER PUSHES CODE
   Developer → git push origin main

7. GITHUB ACTIONS TRIGGERS
   ├─ Checkout code
   ├─ Run tests (pytest, jest)
   ├─ Build Docker image (Buildx)
   ├─ Tag : sha-a1b2c3d
   └─ Push to GHCR

8. ARGOCD IMAGE UPDATER
   ├─ Polls GHCR (every 2 min)
   ├─ Detects new tag : sha-a1b2c3d
   ├─ Pulls image, validates
   ├─ Updates deploy/values.yaml in GitHub
   └─ Commit : "build: update image to sha-a1b2c3d"

9. ARGOCD SYNC
   ├─ Git webhook triggers
   ├─ Renders Helm chart with new values
   ├─ Applies manifests to K8s
   ├─ Rolling update (zero-downtime)
   └─ Health check passes

Status: New version deployed automatically


═══════════════════════════════════════════════════════════
📅 DAY-2 : OPERATIONS (GitOps Write-Back)
═══════════════════════════════════════════════════════════

10. DEVELOPER SCALES APP
    Developer → CMP UI → Slider "5 replicas"

11. CMP WRITES TO GIT
    ├─ Fetch current values.yaml (GitHub API)
    ├─ Parse YAML
    ├─ Update : replicaCount: 5
    ├─ Commit : "ops: scale to 5 replicas"
    └─ Push to main

12. ARGOCD APPLIES
    ├─ Detects config drift
    ├─ Reconciles Deployment
    ├─ Scales pods : 2 → 5
    └─ Status : Synced + Healthy

Git reste la source de vérité !


═══════════════════════════════════════════════════════════
📅 DAY-∞ : MONITORING (Prometheus + ArgoCD + CMP)
═══════════════════════════════════════════════════════════

13. CONTINUOUS MONITORING
    ├─ ArgoCD : Git sync status
    ├─ Prometheus : Metrics scraping
    ├─ CMP : Polls ArgoCD API
    └─ Frontend : Real-time health badges


═══════════════════════════════════════════════════════════
🗑️  DESTRUCTION (Terraform Destroy)
═══════════════════════════════════════════════════════════

14. DEVELOPER DELETES APP
    Developer → CMP UI → "Delete" → Confirm name

15. CMP CLEANUP
    ├─ terraform destroy (micro-state)
    ├─ Removes ArgoCD Application CRD
    ├─ Deletes K8s namespace (cascade)
    ├─ Removes Vault paths
    ├─ Archives GitHub repo (soft delete)
    └─ Removes S3 state file

Status: DELETED → Resources freed


═══════════════════════════════════════════════════════════
🎯 RÉSUMÉ
═══════════════════════════════════════════════════════════

Durée totale Day-0 : ~3-5 minutes
Durée CI/CD : ~2-5 minutes par push
Interventions manuelles : 0 (après Day-0)

Le développeur NE TOUCHE JAMAIS :
❌ Kubernetes API
❌ Helm install
❌ kubectl apply
❌ Docker registry auth
❌ Secret management

Il ne fait QUE :
✅ git push (code)
✅ Éditer values.yaml (scaling, config)
✅ Utiliser le CMP UI (création, monitoring)

C'est ça, une vraie plateforme PaaS ! 🚀
```

---

## 🎉 Ce que Ça Montre

Ces démos montrent que Claude peut :

- ✅ Lire n'importe quel document
- ✅ Combiner plusieurs sources
- ✅ Répondre à des questions complexes
- ✅ Expliquer des workflows complets
- ✅ Faire des comparaisons
- ✅ Synthétiser l'information

**Sans jamais avoir été programmé pour ce projet spécifiquement !**

C'est juste la documentation + MCP = Expert du projet instantané ! 🤯
