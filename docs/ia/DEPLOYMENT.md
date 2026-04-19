# ARCL CMP Deployment Guide

This guide covers deploying ARCL CMP to a k3s cluster using Docker and Helm.

## Prerequisites

- k3s cluster running
- kubectl configured to access your cluster
- Helm 3.x installed
- Docker installed (for local builds)

## Quick Start with Docker Compose

For local development and testing:

```bash
# Copy environment file
cp backend/.env.example backend/.env

# Edit with your credentials
nano backend/.env

# Start services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

Access the application:

- Frontend: http://localhost:3000
- Backend API: http://localhost:8000
- API Docs: http://localhost:8000/docs

## Kubernetes Deployment

### 1. Build and Push Images

#### Option A: Using GitHub Actions (Recommended)

Push a tag to trigger automated builds:

```bash
# Tag for Docker images
git tag v1.0.0
git push origin v1.0.0

# Tag for Helm chart
git tag helm-v1.0.0
git push origin helm-v1.0.0
```

Images will be available at:

- `ghcr.io/3-istor/cmp-backend:v1.0.0`
- `ghcr.io/3-istor/cmp-frontend:v1.0.0`

#### Option B: Manual Build

```bash
# Build backend
docker build -t ghcr.io/3-istor/cmp-backend:latest ./backend

# Build frontend
docker build -t ghcr.io/3-istor/cmp-frontend:latest ./frontend

# Push images
docker push ghcr.io/3-istor/cmp-backend:latest
docker push ghcr.io/3-istor/cmp-frontend:latest
```

### 2. Create Secrets

Create a `values-secrets.yaml` file with your credentials:

```yaml
secrets:
  openstack:
    authUrl: "http://your-openstack:5000/v3"
    username: "your-username"
    password: "your-password"
    projectName: "your-project"
  aws:
    accessKeyId: "your-aws-key"
    secretAccessKey: "your-aws-secret"

ingress:
  hosts:
    - host: arcl-cmp.yourdomain.com
      paths:
        - path: /api
          pathType: Prefix
          service: backend
        - path: /
          pathType: Prefix
          service: frontend
  tls:
    - secretName: arcl-cmp-tls
      hosts:
        - arcl-cmp.yourdomain.com
```

**Important:** Never commit this file! Add it to `.gitignore`.

### 3. Install with Helm

#### From OCI Registry (after helm-v\* tag)

```bash
# Install
helm install arcl-cmp oci://ghcr.io/3-istor/charts/arcl-cmp \
  --version 1.0.0 \
  --values values-secrets.yaml \
  --namespace arcl-cmp \
  --create-namespace

# Upgrade
helm upgrade arcl-cmp oci://ghcr.io/3-istor/charts/arcl-cmp \
  --version 1.0.0 \
  --values values-secrets.yaml \
  --namespace arcl-cmp
```

#### From Local Chart

```bash
# Install
helm install arcl-cmp ./helm/arcl-cmp \
  --values values-secrets.yaml \
  --namespace arcl-cmp \
  --create-namespace

# Upgrade
helm upgrade arcl-cmp ./helm/arcl-cmp \
  --values values-secrets.yaml \
  --namespace arcl-cmp
```

### 4. Verify Deployment

```bash
# Check pods
kubectl get pods -n arcl-cmp

# Check services
kubectl get svc -n arcl-cmp

# Check ingress
kubectl get ingress -n arcl-cmp

# View logs
kubectl logs -f deployment/arcl-cmp-backend -n arcl-cmp
kubectl logs -f deployment/arcl-cmp-frontend -n arcl-cmp
```

### 5. Access the Application

If using ingress with a domain:

```
https://arcl-cmp.yourdomain.com
```

For local testing with port-forward:

```bash
# Frontend
kubectl port-forward svc/arcl-cmp-frontend 3000:3000 -n arcl-cmp

# Backend
kubectl port-forward svc/arcl-cmp-backend 8000:8000 -n arcl-cmp
```

## Configuration Options

### Resource Limits

Adjust in `values.yaml` or override during install:

```bash
helm install arcl-cmp ./helm/arcl-cmp \
  --set resources.backend.limits.memory=2Gi \
  --set resources.backend.limits.cpu=2000m \
  --values values-secrets.yaml
```

### Persistence

By default, uses k3s local-path storage. To use a different storage class:

```yaml
persistence:
  storageClass: "your-storage-class"
  backend:
    size: 20Gi
```

### Autoscaling

Enable horizontal pod autoscaling:

```yaml
autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 5
  targetCPUUtilizationPercentage: 70
```

### Ingress Configuration

For Traefik (default in k3s):

```yaml
ingress:
  enabled: true
  className: "traefik"
  annotations:
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
    traefik.ingress.kubernetes.io/router.middlewares: "default-redirect-https@kubernetescrd"
```

For nginx-ingress:

```yaml
ingress:
  enabled: true
  className: "nginx"
  annotations:
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
```

## CI/CD Workflows

### Automated Builds

The project includes three GitHub Actions workflows:

1. **test.yml** - Runs on every push/PR
   - Lints backend and frontend code
   - Runs tests
   - Validates Docker builds

2. **build-and-push.yml** - Runs on version tags (v*.*.\*)
   - Builds multi-arch Docker images (amd64, arm64)
   - Pushes to GitHub Container Registry
   - Creates GitHub release

3. **helm-release.yml** - Runs on Helm tags (helm-v*.*.\*)
   - Packages Helm chart
   - Pushes to OCI registry
   - Creates GitHub release with install instructions

### Release Process

```bash
# 1. Create and push version tag for Docker images
git tag v1.0.0
git push origin v1.0.0

# Wait for images to build...

# 2. Create and push Helm chart tag
git tag helm-v1.0.0
git push origin helm-v1.0.0

# 3. Deploy to cluster
helm upgrade --install arcl-cmp oci://ghcr.io/3-istor/charts/arcl-cmp \
  --version 1.0.0 \
  --set image.backend.tag=v1.0.0 \
  --set image.frontend.tag=v1.0.0 \
  --values values-secrets.yaml \
  --namespace arcl-cmp
```

## Troubleshooting

### Pods not starting

```bash
# Check pod status
kubectl describe pod <pod-name> -n arcl-cmp

# Check logs
kubectl logs <pod-name> -n arcl-cmp

# Common issues:
# - Missing secrets: Verify values-secrets.yaml
# - Image pull errors: Check image tags and registry access
# - Resource limits: Adjust in values.yaml
```

### Database migrations

```bash
# Run migrations manually
kubectl exec -it deployment/arcl-cmp-backend -n arcl-cmp -- alembic upgrade head
```

### Persistent volume issues

```bash
# Check PVCs
kubectl get pvc -n arcl-cmp

# Check PVs
kubectl get pv

# If using local-path, ensure k3s local-path-provisioner is running
kubectl get pods -n kube-system | grep local-path
```

### Ingress not working

```bash
# Check ingress
kubectl describe ingress arcl-cmp -n arcl-cmp

# Check Traefik (k3s default)
kubectl get pods -n kube-system | grep traefik

# Test internal service
kubectl run -it --rm debug --image=curlimages/curl --restart=Never -- \
  curl http://arcl-cmp-backend.arcl-cmp.svc.cluster.local:8000/health
```

## Backup and Restore

### Backup

```bash
# Backup database
kubectl exec deployment/arcl-cmp-backend -n arcl-cmp -- \
  sqlite3 /app/arcl.db .dump > backup.sql

# Backup Terraform states
kubectl cp arcl-cmp/arcl-cmp-backend-xxx:/app/data ./backup-data
```

### Restore

```bash
# Restore database
kubectl cp backup.sql arcl-cmp/arcl-cmp-backend-xxx:/tmp/backup.sql
kubectl exec deployment/arcl-cmp-backend -n arcl-cmp -- \
  sh -c "sqlite3 /app/arcl.db < /tmp/backup.sql"

# Restore Terraform states
kubectl cp ./backup-data arcl-cmp/arcl-cmp-backend-xxx:/app/data
```

## Uninstall

```bash
# Uninstall Helm release
helm uninstall arcl-cmp -n arcl-cmp

# Delete namespace (including PVCs)
kubectl delete namespace arcl-cmp

# Or keep PVCs for later
helm uninstall arcl-cmp -n arcl-cmp --keep-history
```

## Production Recommendations

1. **Use external database** - Replace SQLite with PostgreSQL
2. **Enable TLS** - Configure cert-manager for automatic certificates
3. **Set resource limits** - Based on your workload
4. **Enable monitoring** - Add Prometheus/Grafana
5. **Configure backups** - Automated backup solution
6. **Use secrets management** - External Secrets Operator or Sealed Secrets
7. **Enable autoscaling** - HPA for variable load
8. **Set up logging** - Centralized logging with Loki or ELK

## Support

For issues and questions:

- GitHub Issues: https://github.com/3-Istor/arcl-cmp/issues
- Documentation: https://github.com/3-Istor/arcl-cmp
