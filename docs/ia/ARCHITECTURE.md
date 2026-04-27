# Architecture - Terraform-Based CMP

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         User Browser                            │
│                  Next.js 15 + React + Tailwind                  │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│  │   Catalog    │  │  Dashboard   │  │ Deployment   │        │
│  │   Browser    │  │   Monitor    │  │   Details    │        │
│  └──────────────┘  └──────────────┘  └──────────────┘        │
│                                                                 │
│         Polls /api/deployments/{id} every 3s for status        │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP/REST
┌────────────────────────────▼────────────────────────────────────┐
│                      FastAPI Backend                            │
│                Python 3.12 · SQLite · Alembic                   │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    API Routers                           │  │
│  │  ┌────────────┐              ┌────────────────┐         │  │
│  │  │  Catalog   │              │  Deployments   │         │  │
│  │  │  Router    │              │    Router      │         │  │
│  │  └─────┬──────┘              └────────┬───────┘         │  │
│  └────────┼─────────────────────────────┼─────────────────┘  │
│           │                             │                     │
│  ┌────────▼─────────────────────────────▼─────────────────┐  │
│  │                   Services Layer                        │  │
│  │                                                         │  │
│  │  ┌──────────────────┐  ┌──────────────────────────┐   │  │
│  │  │   Template       │  │   Terraform              │   │  │
│  │  │   Repository     │  │   Orchestrator           │   │  │
│  │  │                  │  │                          │   │  │
│  │  │ • Clone Git      │  │ • Manage lifecycle       │   │  │
│  │  │ • Sync 24h       │  │ • Update status          │   │  │
│  │  │ • Load manifests │  │ • Capture outputs        │   │  │
│  │  │ • Validate       │  │ • Handle errors          │   │  │
│  │  └────────┬─────────┘  └──────────┬───────────────┘   │  │
│  │           │                       │                    │  │
│  │           │         ┌─────────────▼───────────────┐   │  │
│  │           │         │   Terraform Executor        │   │  │
│  │           │         │                             │   │  │
│  │           │         │ • terraform init            │   │  │
│  │           │         │ • terraform plan            │   │  │
│  │           │         │ • terraform apply           │   │  │
│  │           │         │ • terraform destroy         │   │  │
│  │           │         │ • Capture outputs (JSON)    │   │  │
│  │           │         │ • Manage state files        │   │  │
│  │           │         └─────────────┬───────────────┘   │  │
│  └───────────┼───────────────────────┼───────────────────┘  │
│              │                       │                       │
│  ┌───────────▼───────────────────────▼───────────────────┐  │
│  │                  Data Layer                           │  │
│  │                                                       │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────┐ │  │
│  │  │   SQLite     │  │  Git Repo    │  │  Terraform │ │  │
│  │  │   Database   │  │   Clone      │  │   States   │ │  │
│  │  │              │  │              │  │            │ │  │
│  │  │ • Deployments│  │ • Templates  │  │ • .tfstate │ │  │
│  │  │ • Status     │  │ • Manifests  │  │ • Per app  │ │  │
│  │  │ • Outputs    │  │ • Terraform  │  │ • Isolated │ │  │
│  │  └──────────────┘  └──────────────┘  └────────────┘ │  │
│  └───────────────────────────────────────────────────────┘  │
└────────────────────────────┬────────────────────────────────┘
                             │
                             │ Terraform Provider APIs
                             │
        ┌────────────────────┴────────────────────┐
        │                                         │
┌───────▼──────────┐                  ┌───────────▼──────────┐
│  OpenStack       │                  │   AWS (Future)       │
│                  │                  │                      │
│ • Compute        │                  │ • EC2                │
│ • Network        │                  │ • VPC                │
│ • Storage        │                  │ • ELB                │
│ • Load Balancer  │                  │ • Auto Scaling       │
└──────────────────┘                  └──────────────────────┘
```

## Component Details

### Frontend (Next.js)

**Responsibilities:**

- Display template catalog
- Show deployment dashboard
- Monitor deployment progress
- Display Terraform outputs
- Handle user interactions

**Key Features:**

- Real-time polling (3s interval)
- Status visualization
- Output display
- Template browsing

### Backend (FastAPI)

**Responsibilities:**

- Serve REST API
- Manage deployments
- Execute Terraform
- Track status
- Store data

**Key Components:**

#### 1. Template Repository Service

```python
class TemplateRepository:
    - Clone Git repository
    - Sync every 24 hours
    - Load manifests
    - Validate templates
    - Cache templates
```

#### 2. Terraform Executor

```python
class TerraformExecutor:
    - Execute Terraform commands
    - Manage state files
    - Capture outputs
    - Handle errors
```

#### 3. Terraform Orchestrator

```python
def run_deployment():
    1. Initialize Terraform
    2. Plan deployment
    3. Apply configuration
    4. Capture outputs
    5. Update status
```

### Data Layer

#### SQLite Database

```sql
deployments:
  - id
  - name
  - template_id
  - status (PENDING → INITIALIZING → PLANNING → DEPLOYING → RUNNING)
  - terraform_outputs (JSON)
  - terraform_state_path
  - resource_count
  - template_name, icon, category
  - created_at, updated_at
```

#### Git Repository

```
data/templates/
└── templates/
    ├── openstack-nginx/
    │   ├── manifest.json
    │   ├── main.tf
    │   ├── variables.tf
    │   ├── outputs.tf
    │   └── icon.png
    └── openstack-web-git/
        ├── manifest.json
        ├── main.tf
        ├── variables.tf
        └── outputs.tf
```

#### Terraform State

```
data/terraform_states/
├── deployment-1/
│   ├── .terraform/
│   └── terraform.tfstate
└── deployment-2/
    ├── .terraform/
    └── terraform.tfstate
```

## Data Flow

### Deployment Creation

```
1. User selects template
   ↓
2. Frontend: POST /api/deployments/
   {
     "name": "my-app",
     "template_id": "openstack-nginx",
     "app_config": {"instance_count": 2}
   }
   ↓
3. Backend creates deployment record
   Status: PENDING
   ↓
4. Background task starts
   ↓
5. Terraform Orchestrator:
   a. Get template from repository
   b. Create Terraform executor
   c. Status: INITIALIZING
      → terraform init
   d. Status: PLANNING
      → terraform plan
   e. Status: DEPLOYING
      → terraform apply
   f. Capture outputs
   g. Status: RUNNING
   ↓
6. Frontend polls and displays:
   - Status updates
   - Terraform outputs
   - Resource count
```

### Deployment Deletion

```
1. User confirms deletion
   ↓
2. Frontend: DELETE /api/deployments/{id}
   ↓
3. Backend updates status: DELETING
   ↓
4. Background task:
   a. Get template
   b. Create executor
   c. terraform destroy
   ↓
5. Status: DELETED
   ↓
6. Frontend removes from dashboard
```

## Status Flow

```
PENDING
  ↓
INITIALIZING (terraform init)
  ↓
PLANNING (terraform plan)
  ↓
DEPLOYING (terraform apply)
  ↓
RUNNING (success)

OR

FAILED (error at any step)
```

```
RUNNING
  ↓
DELETING (terraform destroy)
  ↓
DELETED
```

## Template Manifest Structure

```json
{
  "enabled": true,
  "id": "template-id",
  "name": "Template Name",
  "description": "Description",
  "icon": "🌐",
  "image_path": "icon.png",
  "category": "Web",
  "variables": [
    {
      "name": "var_name",
      "label": "Display Label",
      "type": "text|number|select",
      "default": "value",
      "required": true,
      "options": ["opt1", "opt2"]
    }
  ]
}
```

## Terraform Template Structure

```hcl
# variables.tf
variable "instance_count" {
  type    = number
  default = 2
}

# main.tf
resource "openstack_compute_instance_v2" "web" {
  count = var.instance_count
  # ...
}

# outputs.tf
output "loadbalancer_ip" {
  value = openstack_networking_floatingip_v2.lb_ip.address
}
```

## API Endpoints

### Catalog

- `GET /api/catalog/` - List templates
- `GET /api/catalog/{id}` - Get template
- `POST /api/catalog/sync` - Force sync

### Deployments

- `GET /api/deployments/` - List deployments
- `POST /api/deployments/` - Create deployment
- `GET /api/deployments/{id}` - Get deployment
- `GET /api/deployments/{id}/outputs` - Get outputs
- `DELETE /api/deployments/{id}` - Delete deployment

## Security Considerations

1. **Credentials**: Stored in `.env`, never in code
2. **State Files**: Local, isolated per deployment
3. **API**: CORS configured for frontend
4. **Terraform**: Runs with backend service account
5. **Git**: Public repository, read-only access

## Scalability Considerations

1. **Concurrent Deployments**: Currently no locking (future: implement locking)
2. **State Management**: Local files (future: remote backend)
3. **Template Sync**: 24h interval (configurable)
4. **Database**: SQLite (future: PostgreSQL for production)
5. **Background Tasks**: FastAPI BackgroundTasks (future: Celery)

## Monitoring & Logging

1. **Deployment Status**: Real-time via polling
2. **Terraform Output**: Captured in `step_message`
3. **Logs**: Python logging to console
4. **Errors**: Captured in deployment record
5. **State**: Terraform state files

## Future Enhancements

1. **Remote State Backend** (S3, Terraform Cloud)
2. **State Locking** (DynamoDB, Consul)
3. **WebSocket** for real-time logs
4. **Multi-user** with RBAC
5. **Cost Estimation** integration
6. **Template Validation** on sync
7. **Deployment History** and audit logs
8. **Resource Tagging** for tracking

---

This architecture provides a flexible, scalable foundation for multi-cloud infrastructure deployment with Terraform.
