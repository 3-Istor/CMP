# ARCL CMP Helm Chart

Helm chart for deploying ARCL Hybrid Cloud Management Platform on Kubernetes.

## Prerequisites

- Kubernetes 1.19+
- Helm 3.0+
- PV provisioner support in the underlying infrastructure (for persistence)

## Installing the Chart

### From OCI Registry

```bash
helm install arcl-cmp oci://ghcr.io/3-istor/charts/arcl-cmp \
  --version 1.0.0 \
  --namespace arcl-cmp \
  --create-namespace \
  --set secrets.openstack.authUrl="http://your-openstack:5000/v3" \
  --set secrets.openstack.username="your-username" \
  --set secrets.openstack.password="your-password" \
  --set secrets.openstack.projectName="your-project"
```

### From Local Chart

```bash
helm install arcl-cmp ./helm/arcl-cmp \
  --namespace arcl-cmp \
  --create-namespace \
  --values my-values.yaml
```

## Configuration

The following table lists the configurable parameters and their default values.

| Parameter                          | Description               | Default                             |
| ---------------------------------- | ------------------------- | ----------------------------------- |
| `replicaCount`                     | Number of replicas        | `1`                                 |
| `image.backend.repository`         | Backend image repository  | `ghcr.io/3-istor/arcl-cmp-backend`  |
| `image.backend.tag`                | Backend image tag         | `latest`                            |
| `image.frontend.repository`        | Frontend image repository | `ghcr.io/3-istor/arcl-cmp-frontend` |
| `image.frontend.tag`               | Frontend image tag        | `latest`                            |
| `service.type`                     | Kubernetes service type   | `ClusterIP`                         |
| `ingress.enabled`                  | Enable ingress            | `true`                              |
| `ingress.className`                | Ingress class name        | `traefik`                           |
| `ingress.hosts[0].host`            | Hostname                  | `arcl-cmp.example.com`              |
| `persistence.enabled`              | Enable persistence        | `true`                              |
| `persistence.storageClass`         | Storage class             | `local-path`                        |
| `persistence.backend.size`         | Backend storage size      | `10Gi`                              |
| `resources.backend.limits.cpu`     | Backend CPU limit         | `1000m`                             |
| `resources.backend.limits.memory`  | Backend memory limit      | `1Gi`                               |
| `resources.frontend.limits.cpu`    | Frontend CPU limit        | `500m`                              |
| `resources.frontend.limits.memory` | Frontend memory limit     | `512Mi`                             |

### Secrets Configuration

Create a `values-secrets.yaml` file:

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
```

Then install with:

```bash
helm install arcl-cmp ./helm/arcl-cmp -f values-secrets.yaml
```

## Upgrading

```bash
helm upgrade arcl-cmp oci://ghcr.io/3-istor/charts/arcl-cmp \
  --version 1.0.0 \
  --reuse-values
```

## Uninstalling

```bash
helm uninstall arcl-cmp -n arcl-cmp
```

## Examples

### Minimal Installation

```bash
helm install arcl-cmp ./helm/arcl-cmp \
  --set secrets.openstack.authUrl="http://openstack:5000/v3" \
  --set secrets.openstack.username="admin" \
  --set secrets.openstack.password="secret" \
  --set secrets.openstack.projectName="demo"
```

### Production Installation

```yaml
# production-values.yaml
replicaCount: 2

image:
  backend:
    tag: "v1.0.0"
  frontend:
    tag: "v1.0.0"

ingress:
  enabled: true
  className: "nginx"
  annotations:
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
  hosts:
    - host: arcl.company.com
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
        - arcl.company.com

persistence:
  storageClass: "fast-ssd"
  backend:
    size: 50Gi

resources:
  backend:
    limits:
      cpu: 2000m
      memory: 2Gi
    requests:
      cpu: 500m
      memory: 1Gi
  frontend:
    limits:
      cpu: 1000m
      memory: 1Gi
    requests:
      cpu: 200m
      memory: 512Mi

autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 5
  targetCPUUtilizationPercentage: 70
```

Install:

```bash
helm install arcl-cmp ./helm/arcl-cmp \
  -f production-values.yaml \
  -f values-secrets.yaml \
  --namespace arcl-cmp \
  --create-namespace
```

## Support

For more information, visit: https://github.com/3-Istor/arcl-cmp
