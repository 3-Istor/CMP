# Quick Start: Docker & Kubernetes

Get ARCL CMP running in minutes with Docker or Kubernetes.

## Option 1: Docker Compose (Fastest)

Perfect for local development and testing.

```bash
# 1. Clone and setup
git clone https://github.com/3-Istor/arcl-cmp.git
cd arcl-cmp

# 2. Configure credentials
cp backend/.env.example backend/.env
nano backend/.env  # Add your OpenStack/AWS credentials

# 3. Start services
docker-compose up -d

# 4. Access application
# Frontend: http://localhost:3000
# Backend:  http://localhost:8000
# API Docs: http://localhost:8000/docs
```

That's it! 🎉

## Option 2: Kubernetes with Helm

For production deployments on k3s or any Kubernetes cluster.

### Prerequisites

- k3s or Kubernetes cluster running
- kubectl configured
- Helm 3.x installed

### Deploy

```bash
# 1. Clone repository
git clone https://github.com/3-Istor/arcl-cmp.git
cd arcl-cmp

# 2. Create secrets file
cp values-secrets.yaml.example values-secrets.yaml
nano values-secrets.yaml  # Add your credentials and domain

# 3. Deploy with script
chmod +x scripts/deploy-k3s.sh
./scripts/deploy-k3s.sh

# Or manually with Helm
helm install arcl-cmp ./helm/arcl-cmp \
  --namespace arcl-cmp \
  --create-namespace \
  --values values-secrets.yaml
```

### Access Application

```bash
# Get ingress URL
kubectl get ingress -n arcl-cmp

# Or use port-forward for testing
kubectl port-forward svc/arcl-cmp-frontend 3000:3000 -n arcl-cmp
# Then visit: http://localhost:3000
```

## Option 3: From GitHub Container Registry

Use pre-built images from GitHub releases.

### Docker Compose with Registry Images

```yaml
# docker-compose.yml
version: "3.8"
services:
  backend:
    image: ghcr.io/3-istor/arcl-cmp-backend:latest
    ports:
      - "8000:8000"
    environment:
      - OS_AUTH_URL=${OS_AUTH_URL}
      - OS_USERNAME=${OS_USERNAME}
      - OS_PASSWORD=${OS_PASSWORD}
      - OS_PROJECT_NAME=${OS_PROJECT_NAME}

  frontend:
    image: ghcr.io/3-istor/arcl-cmp-frontend:latest
    ports:
      - "3000:3000"
    environment:
      - NEXT_PUBLIC_API_URL=http://backend:8000/api
```

### Helm with Registry Images

```bash
helm install arcl-cmp oci://ghcr.io/3-istor/charts/arcl-cmp \
  --version 1.0.0 \
  --namespace arcl-cmp \
  --create-namespace \
  --values values-secrets.yaml
```

## CI/CD Setup

Automate builds and deployments with GitHub Actions.

### 1. Enable GitHub Actions

Already configured! Just push tags to trigger builds.

### 2. Build Docker Images

```bash
# Create and push version tag
git tag v1.0.0
git push origin v1.0.0

# GitHub Actions will:
# - Build multi-arch images (amd64, arm64)
# - Push to ghcr.io/3-istor/arcl-cmp-backend:v1.0.0
# - Push to ghcr.io/3-istor/arcl-cmp-frontend:v1.0.0
# - Create GitHub release
```

### 3. Release Helm Chart

```bash
# Create and push Helm tag
git tag helm-v1.0.0
git push origin helm-v1.0.0

# GitHub Actions will:
# - Package Helm chart
# - Push to oci://ghcr.io/3-istor/charts/arcl-cmp
# - Create GitHub release with install instructions
```

### 4. Deploy to Cluster

```bash
# Deploy latest release
helm upgrade --install arcl-cmp oci://ghcr.io/3-istor/charts/arcl-cmp \
  --version 1.0.0 \
  --set image.backend.tag=v1.0.0 \
  --set image.frontend.tag=v1.0.0 \
  --values values-secrets.yaml \
  --namespace arcl-cmp
```

## Common Commands

### Docker Compose

```bash
# Start
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down

# Rebuild
docker-compose up -d --build
```

### Kubernetes

```bash
# Check status
kubectl get all -n arcl-cmp

# View logs
kubectl logs -f deployment/arcl-cmp-backend -n arcl-cmp

# Port forward
kubectl port-forward svc/arcl-cmp-frontend 3000:3000 -n arcl-cmp

# Restart
kubectl rollout restart deployment/arcl-cmp-backend -n arcl-cmp

# Uninstall
helm uninstall arcl-cmp -n arcl-cmp
```

## Troubleshooting

### Docker Compose Issues

```bash
# Check container status
docker-compose ps

# View logs
docker-compose logs backend
docker-compose logs frontend

# Restart specific service
docker-compose restart backend

# Clean restart
docker-compose down -v
docker-compose up -d
```

### Kubernetes Issues

```bash
# Check pod status
kubectl get pods -n arcl-cmp
kubectl describe pod <pod-name> -n arcl-cmp

# Check logs
kubectl logs <pod-name> -n arcl-cmp

# Check events
kubectl get events -n arcl-cmp --sort-by='.lastTimestamp'

# Test connectivity
kubectl exec -it deployment/arcl-cmp-backend -n arcl-cmp -- \
  curl http://localhost:8000/health
```

### Common Problems

**Problem:** Images not pulling

```bash
# Make packages public in GitHub
# Or add imagePullSecrets to Helm values
```

**Problem:** Pods stuck in Pending

```bash
# Check PVC status
kubectl get pvc -n arcl-cmp

# Check node resources
kubectl describe nodes
```

**Problem:** Can't access via ingress

```bash
# Check ingress controller
kubectl get pods -n kube-system | grep traefik

# Check ingress configuration
kubectl describe ingress arcl-cmp -n arcl-cmp

# Add to /etc/hosts for local testing
echo "127.0.0.1 arcl-cmp.local" | sudo tee -a /etc/hosts
```

## Next Steps

- Read [DEPLOYMENT.md](DEPLOYMENT.md) for detailed deployment guide
- Check [DOCKER_KUBERNETES.md](DOCKER_KUBERNETES.md) for command reference
- See [.github/workflows/README.md](.github/workflows/README.md) for CI/CD details
- Review [helm/arcl-cmp/README.md](helm/arcl-cmp/README.md) for Helm chart options

## Support

- GitHub Issues: https://github.com/3-Istor/arcl-cmp/issues
- Documentation: https://github.com/3-Istor/arcl-cmp
