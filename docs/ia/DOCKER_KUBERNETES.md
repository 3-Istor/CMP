# Docker & Kubernetes Quick Reference

Quick commands for working with ARCL CMP in Docker and Kubernetes.

## Docker Commands

### Build Images

```bash
# Using helper script
./scripts/build-images.sh

# Or manually
docker build -t arcl-cmp-backend:latest ./backend
docker build -t arcl-cmp-frontend:latest ./frontend
```

### Run with Docker Compose

```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down

# Rebuild and restart
docker-compose up -d --build

# Remove volumes
docker-compose down -v
```

### Individual Container Commands

```bash
# Run backend only
docker run -d -p 8000:8000 \
  -e OS_AUTH_URL="http://openstack:5000/v3" \
  -e OS_USERNAME="admin" \
  -e OS_PASSWORD="secret" \
  -e OS_PROJECT_NAME="demo" \
  arcl-cmp-backend:latest

# Run frontend only
docker run -d -p 3000:3000 \
  -e NEXT_PUBLIC_API_URL="http://localhost:8000/api" \
  arcl-cmp-frontend:latest

# Execute command in running container
docker exec -it arcl-backend bash

# View logs
docker logs -f arcl-backend
```

## Kubernetes Commands

### Deploy with Helm

```bash
# Using helper script
./scripts/deploy-k3s.sh

# Or manually
helm install arcl-cmp ./helm/arcl-cmp \
  --namespace arcl-cmp \
  --create-namespace \
  --values values-secrets.yaml
```

### Manage Deployments

```bash
# Upgrade
helm upgrade arcl-cmp ./helm/arcl-cmp \
  --namespace arcl-cmp \
  --values values-secrets.yaml

# Rollback
helm rollback arcl-cmp -n arcl-cmp

# Uninstall
helm uninstall arcl-cmp -n arcl-cmp

# List releases
helm list -n arcl-cmp

# Get values
helm get values arcl-cmp -n arcl-cmp
```

### Pod Management

```bash
# List pods
kubectl get pods -n arcl-cmp

# Describe pod
kubectl describe pod <pod-name> -n arcl-cmp

# View logs
kubectl logs -f deployment/arcl-cmp-backend -n arcl-cmp
kubectl logs -f deployment/arcl-cmp-frontend -n arcl-cmp

# Execute command in pod
kubectl exec -it deployment/arcl-cmp-backend -n arcl-cmp -- bash

# Port forward
kubectl port-forward svc/arcl-cmp-frontend 3000:3000 -n arcl-cmp
kubectl port-forward svc/arcl-cmp-backend 8000:8000 -n arcl-cmp
```

### Service & Ingress

```bash
# List services
kubectl get svc -n arcl-cmp

# List ingress
kubectl get ingress -n arcl-cmp

# Describe ingress
kubectl describe ingress arcl-cmp -n arcl-cmp

# Test service internally
kubectl run -it --rm debug --image=curlimages/curl --restart=Never -- \
  curl http://arcl-cmp-backend.arcl-cmp.svc.cluster.local:8000/health
```

### Storage

```bash
# List PVCs
kubectl get pvc -n arcl-cmp

# List PVs
kubectl get pv

# Describe PVC
kubectl describe pvc arcl-cmp-backend-data -n arcl-cmp
```

### Debugging

```bash
# Get all resources
kubectl get all -n arcl-cmp

# Check events
kubectl get events -n arcl-cmp --sort-by='.lastTimestamp'

# Check pod status
kubectl get pods -n arcl-cmp -o wide

# Check resource usage
kubectl top pods -n arcl-cmp
kubectl top nodes

# Restart deployment
kubectl rollout restart deployment/arcl-cmp-backend -n arcl-cmp
kubectl rollout restart deployment/arcl-cmp-frontend -n arcl-cmp

# Check rollout status
kubectl rollout status deployment/arcl-cmp-backend -n arcl-cmp
```

### Secrets Management

```bash
# View secret (base64 encoded)
kubectl get secret arcl-cmp-secret -n arcl-cmp -o yaml

# Decode secret value
kubectl get secret arcl-cmp-secret -n arcl-cmp -o jsonpath='{.data.os-password}' | base64 -d

# Update secret
kubectl create secret generic arcl-cmp-secret \
  --from-literal=os-password='new-password' \
  --namespace arcl-cmp \
  --dry-run=client -o yaml | kubectl apply -f -

# Delete and recreate (with Helm)
kubectl delete secret arcl-cmp-secret -n arcl-cmp
helm upgrade arcl-cmp ./helm/arcl-cmp -n arcl-cmp --values values-secrets.yaml
```

## CI/CD Commands

### Trigger Builds

```bash
# Build and push Docker images
git tag v1.0.0
git push origin v1.0.0

# Release Helm chart
git tag helm-v1.0.0
git push origin helm-v1.0.0

# Delete tag (if needed)
git tag -d v1.0.0
git push origin :refs/tags/v1.0.0
```

### Deploy from Registry

```bash
# Install from OCI registry
helm install arcl-cmp oci://ghcr.io/3-istor/charts/arcl-cmp \
  --version 1.0.0 \
  --namespace arcl-cmp \
  --create-namespace \
  --values values-secrets.yaml

# Upgrade with specific image versions
helm upgrade arcl-cmp oci://ghcr.io/3-istor/charts/arcl-cmp \
  --version 1.0.0 \
  --set image.backend.tag=v1.0.0 \
  --set image.frontend.tag=v1.0.0 \
  --reuse-values
```

## Monitoring

### Check Application Health

```bash
# Backend health
curl http://localhost:8000/health

# Or in cluster
kubectl exec -it deployment/arcl-cmp-backend -n arcl-cmp -- \
  curl http://localhost:8000/health

# Frontend health
curl http://localhost:3000

# Check API
curl http://localhost:8000/api/catalog/
```

### Watch Resources

```bash
# Watch pods
watch kubectl get pods -n arcl-cmp

# Watch all resources
watch kubectl get all -n arcl-cmp

# Stream logs from all pods
kubectl logs -f -l app.kubernetes.io/instance=arcl-cmp -n arcl-cmp --all-containers=true
```

## Backup & Restore

### Backup

```bash
# Backup database
kubectl exec deployment/arcl-cmp-backend -n arcl-cmp -- \
  sqlite3 /app/arcl.db .dump > backup-$(date +%Y%m%d).sql

# Backup entire data directory
kubectl cp arcl-cmp/$(kubectl get pod -n arcl-cmp -l app.kubernetes.io/component=backend -o jsonpath='{.items[0].metadata.name}'):/app/data ./backup-data-$(date +%Y%m%d)

# Backup Helm values
helm get values arcl-cmp -n arcl-cmp > backup-values.yaml
```

### Restore

```bash
# Restore database
kubectl cp backup.sql arcl-cmp/$(kubectl get pod -n arcl-cmp -l app.kubernetes.io/component=backend -o jsonpath='{.items[0].metadata.name}'):/tmp/backup.sql

kubectl exec deployment/arcl-cmp-backend -n arcl-cmp -- \
  sh -c "sqlite3 /app/arcl.db < /tmp/backup.sql"

# Restore data directory
kubectl cp ./backup-data arcl-cmp/$(kubectl get pod -n arcl-cmp -l app.kubernetes.io/component=backend -o jsonpath='{.items[0].metadata.name}'):/app/data
```

## Troubleshooting

### Common Issues

```bash
# Pod stuck in Pending
kubectl describe pod <pod-name> -n arcl-cmp
# Check: PVC binding, resource limits, node capacity

# Pod CrashLoopBackOff
kubectl logs <pod-name> -n arcl-cmp --previous
# Check: application errors, missing env vars, health checks

# ImagePullBackOff
kubectl describe pod <pod-name> -n arcl-cmp
# Check: image name, tag, registry access, imagePullSecrets

# Service not accessible
kubectl get endpoints -n arcl-cmp
# Check: pod labels match service selector, pods are ready

# Ingress not working
kubectl describe ingress arcl-cmp -n arcl-cmp
# Check: ingress controller, DNS, TLS certificates
```

### Reset Everything

```bash
# Complete cleanup
helm uninstall arcl-cmp -n arcl-cmp
kubectl delete namespace arcl-cmp
kubectl delete pv <pv-name>  # if needed

# Fresh install
./scripts/deploy-k3s.sh
```

## Performance Tuning

### Scale Deployments

```bash
# Manual scaling
kubectl scale deployment arcl-cmp-backend --replicas=3 -n arcl-cmp
kubectl scale deployment arcl-cmp-frontend --replicas=3 -n arcl-cmp

# Or with Helm
helm upgrade arcl-cmp ./helm/arcl-cmp \
  --set replicaCount=3 \
  --reuse-values
```

### Enable Autoscaling

```bash
# Via Helm values
helm upgrade arcl-cmp ./helm/arcl-cmp \
  --set autoscaling.enabled=true \
  --set autoscaling.minReplicas=2 \
  --set autoscaling.maxReplicas=5 \
  --set autoscaling.targetCPUUtilizationPercentage=70 \
  --reuse-values

# Check HPA
kubectl get hpa -n arcl-cmp
kubectl describe hpa arcl-cmp-backend -n arcl-cmp
```

### Adjust Resources

```bash
helm upgrade arcl-cmp ./helm/arcl-cmp \
  --set resources.backend.limits.memory=2Gi \
  --set resources.backend.limits.cpu=2000m \
  --set resources.backend.requests.memory=1Gi \
  --set resources.backend.requests.cpu=500m \
  --reuse-values
```

## Useful Aliases

Add to your `~/.bashrc` or `~/.zshrc`:

```bash
# Kubernetes
alias k='kubectl'
alias kgp='kubectl get pods'
alias kgs='kubectl get svc'
alias kgi='kubectl get ingress'
alias kl='kubectl logs -f'
alias kx='kubectl exec -it'
alias kn='kubectl config set-context --current --namespace'

# ARCL CMP specific
alias arcl-logs-backend='kubectl logs -f deployment/arcl-cmp-backend -n arcl-cmp'
alias arcl-logs-frontend='kubectl logs -f deployment/arcl-cmp-frontend -n arcl-cmp'
alias arcl-shell='kubectl exec -it deployment/arcl-cmp-backend -n arcl-cmp -- bash'
alias arcl-status='kubectl get all -n arcl-cmp'
```
