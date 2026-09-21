# CNP Backend - FastAPI Application

Backend du Cloud Native Platform (CNP) - Internal Developer Portal.

## 🚀 Quick Start

```bash
# Installation
poetry install

# Configuration
cp .env.example .env
# Éditer .env avec vos credentials

# Migration de la base de données
poetry run alembic upgrade head

# Lancer le serveur
poetry run uvicorn app.main:app --reload
```

## 📋 Prérequis

- Python 3.11+
- Poetry
- PostgreSQL ou SQLite
- GitHub App configurée (pour Kubernetes deployments)
- AWS credentials (pour legacy deployments)
- OpenStack credentials (pour legacy deployments)

## 🏗️ Architecture

### Multi-Provider Support (Phase 3)

Le backend supporte deux types de déploiements :

- **Legacy Hybrid** : OpenStack VMs + AWS Auto Scaling Groups
- **Kubernetes** : GitHub + Terraform + ArgoCD (GitOps)

### Structure

```
backend/
├── app/
│   ├── core/           # Configuration, database
│   ├── models/         # SQLAlchemy models
│   ├── routers/        # FastAPI routes
│   ├── services/       # Business logic
│   │   ├── github_service.py      # GitHub App integration
│   │   ├── saga_orchestrator.py   # Multi-provider orchestration
│   │   └── monitoring_service.py  # Health monitoring
│   └── terraform/      # Terraform modules
│       └── github_bootstrap/      # Kubernetes Day-0 provisioning
├── alembic/            # Database migrations
├── data/               # Templates and static data
└── tests/              # Unit tests
```

## 🔧 Configuration

### Fichier `.env`

```bash
# Database
DATABASE_URL=sqlite:///./cmp.db

# GitHub App (Phase 3 - Kubernetes)
GITHUB_APP_ID=3836905
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"
GITHUB_INSTALLATION_ID=135177507  # Optionnel

# AWS (Legacy)
AWS_ACCESS_KEY_ID=your_key
AWS_SECRET_ACCESS_KEY=your_secret
AWS_REGION=eu-west-3

# OpenStack (Legacy)
OS_AUTH_URL=https://openstack.example.com:5000/v3
OS_USERNAME=your_username
OS_PASSWORD=your_password
OS_PROJECT_NAME=your_project

# Terraform S3 Backend
TF_BACKEND_S3_ENABLED=true
TF_BACKEND_S3_BUCKET=3-istor-tf-infra-aws
TF_BACKEND_S3_DYNAMODB_TABLE=terraform-state-lock
```

## 🛠️ Outils de Debug

### GitHub Integration

```bash
# Générer un token GitHub App
poetry run python debug_github_token.py 135177507

# Tester l'API GitHub
./test_github_api.sh 135177507

# Tests unitaires
poetry run python test_github_service.py
```

### Phase 3 Verification

```bash
# Vérifier l'installation complète
poetry run python verify_phase3.py

# Setup automatique
./setup_phase3.sh
```

**Documentation complète** : `README_DEBUG_TOOLS.md`

## 📚 Documentation

### Guides Principaux

| Document                     | Description                        |
| ---------------------------- | ---------------------------------- |
| **PHASE3_COMPLETE.md**       | Guide complet Phase 3 (Kubernetes) |
| **QUICKSTART_PHASE3.md**     | Setup rapide Phase 3 (5 minutes)   |
| **PHASE3_IMPLEMENTATION.md** | Détails techniques Phase 3         |
| **PHASE3_ARCHITECTURE.md**   | Diagrammes et architecture         |

### Guides Debug

| Document                       | Description                       |
| ------------------------------ | --------------------------------- |
| **README_DEBUG_TOOLS.md**      | Index des outils de debug         |
| **GITHUB_DEBUG_TOOLS.md**      | Guide complet GitHub integration  |
| **DEBUG_GITHUB_TOKEN.md**      | Documentation générateur de token |
| **GITHUB_TOKEN_QUICKSTART.md** | Guide rapide token (2 min)        |

### Documentation API

Voir `.kiro/steering/docs/05-backend-api/01-deployment-api.md`

## 🧪 Tests

```bash
# Tests unitaires
poetry run pytest

# Tests d'intégration
poetry run pytest tests/integration/

# Coverage
poetry run pytest --cov=app --cov-report=html
```

## 🚢 Déploiement

### Development

```bash
poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Production

```bash
poetry run gunicorn app.main:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000
```

## 📊 API Endpoints

### Deployments

- `GET /api/deployments` - Liste des déploiements
- `GET /api/deployments/{id}` - Détails d'un déploiement
- `POST /api/deployments` - Créer un déploiement
- `DELETE /api/deployments/{id}` - Supprimer un déploiement

### Templates

- `GET /api/templates` - Liste des templates
- `GET /api/templates/{id}` - Détails d'un template

### Health

- `GET /health` - Health check
- `GET /api/health/infrastructure` - Infrastructure status

**Documentation complète** : http://localhost:8000/docs (Swagger UI)

## 🔐 Sécurité

### GitHub App

- Tokens JWT signés (expire 10 min)
- Installation tokens (expire 1 heure)
- Clé privée stockée dans `.env` (jamais commitée)

### Terraform State

- Encryption at rest (S3)
- State locking (DynamoDB)
- Micro-state pattern (isolation par app)

### Secrets

- Vault pour les secrets applicatifs
- Environment variables pour les credentials
- Pas de secrets en base de données

## 🐛 Troubleshooting

### Erreur : "GITHUB_APP_PRIVATE_KEY not configured"

```bash
# Vérifier .env
grep GITHUB_APP_PRIVATE_KEY .env

# Télécharger la clé depuis GitHub
# Settings > Developer settings > GitHub Apps > CNP-Portal > Private keys
```

### Erreur : "Database migration failed"

```bash
# Réinitialiser la base
rm cmp.db
poetry run alembic upgrade head
```

### Erreur : "Terraform execution failed"

```bash
# Vérifier les credentials AWS
aws sts get-caller-identity

# Vérifier le bucket S3
aws s3 ls s3://3-istor-tf-infra-aws/
```

## 📈 Monitoring

### Logs

```bash
# Logs en temps réel
tail -f logs/cmp.log

# Logs Terraform
tail -f /tmp/terraform-*.log
```

### Métriques

- Deployment success rate
- Terraform execution time
- GitHub API rate limits
- Database query performance

## 🤝 Contribution

### Workflow

1. Créer une branche : `git checkout -b feature/my-feature`
2. Développer et tester : `poetry run pytest`
3. Commit : `git commit -m "feat: my feature"`
4. Push : `git push origin feature/my-feature`
5. Créer une Pull Request

### Standards

- **Code** : Black formatter, Pylint
- **Commits** : Conventional Commits
- **Tests** : Coverage > 80%
- **Documentation** : Docstrings obligatoires

## 📞 Support

### Questions Backend

- **Setup** : `QUICKSTART_PHASE3.md`
- **Architecture** : `PHASE3_ARCHITECTURE.md`
- **API** : `.kiro/steering/docs/05-backend-api/`

### Questions GitHub

- **Debug** : `GITHUB_DEBUG_TOOLS.md`
- **Token** : `DEBUG_GITHUB_TOKEN.md`
- **Tests** : `test_github_service.py`

### Questions Générales

- **Roadmap** : `.kiro/steering/docs/README_ROADMAP.md`
- **Changelog** : `.kiro/steering/docs/CHANGELOG.md`
- **Architecture** : `.kiro/steering/docs/01-architecture/`

## 🔗 Liens Utiles

- **GitHub App** : https://github.com/settings/apps/cnp-portal
- **API Docs** : http://localhost:8000/docs
- **Frontend** : `../frontend/`
- **Documentation** : `../.kiro/steering/docs/`

---

**Version** : Phase 3 (Kubernetes Support) + MCP Integration
**Dernière mise à jour** : 2026-06-26
**Équipe** : CNP Platform Team

---

## 🆕 New Features (2026-06-26)

### ✅ OAuth2 Swagger UI

Interactive API testing with Keycloak authentication:

```bash
# Start backend
poetry run uvicorn app.main:app --reload

# Visit http://localhost:8000/docs
# Click "Authorize" → Keycloak SSO → Test endpoints
```

**Benefits:**

- No Postman needed
- Real production auth
- Interactive testing

### ✅ MCP Server (AI Integration)

AI assistants can read docs and call APIs:

```bash
# Quick setup
./setup_mcp.sh

# Test
poetry run python test_mcp_server.py
```

**Documentation:**

- **Quick Start** : [QUICK_START_MCP.md](QUICK_START_MCP.md)
- **Complete Guide** : [MCP_SERVER_README.md](MCP_SERVER_README.md)
- **Implementation** : [MCP_IMPLEMENTATION_SUMMARY.md](MCP_IMPLEMENTATION_SUMMARY.md)
- **Integration Complete** : [MCP_INTEGRATION_COMPLETE.md](../MCP_INTEGRATION_COMPLETE.md)
- **Architecture** : [.kiro/steering/docs/05-cmp-backend-api/11-cmp-mcp-integration.md](../.kiro/steering/docs/05-cmp-backend-api/11-cmp-mcp-integration.md)

**Example AI Workflows:**

```
Claude: "Read docs://01-architecture/01-system-overview and explain CNP"
Claude: "List all my deployments (token: eyJhbG...)"
Claude: "Deploy 'billing-api' in project 'finance' with 3 replicas"
```
