# GitHub Actions Workflows

This directory contains CI/CD workflows for CMP.

## Workflows

### 1. test.yml

**Trigger:** Push to main/develop, Pull Requests

Runs on every code change to ensure quality:

- Backend linting (black, isort, pylint)
- Frontend linting (eslint)
- Frontend build test
- Docker build validation

### 2. build-and-push.yml

**Trigger:** Version tags (v*.*.\*)

Builds and publishes Docker images:

- Multi-architecture builds (amd64, arm64)
- Pushes to GitHub Container Registry
- Tags: version, major.minor, major, latest
- Creates GitHub release with image references

**Usage:**

```bash
git tag v1.0.0
git push origin v1.0.0
```

**Output:**

- `ghcr.io/3-istor/cmp-backend:v1.0.0`
- `ghcr.io/3-istor/cmp-backend:1.0`
- `ghcr.io/3-istor/cmp-backend:1`
- `ghcr.io/3-istor/cmp-backend:latest`
- `ghcr.io/3-istor/cmp-frontend:v1.0.0`
- `ghcr.io/3-istor/cmp-frontend:1.0`
- `ghcr.io/3-istor/cmp-frontend:1`
- `ghcr.io/3-istor/cmp-frontend:latest`

### 3. helm-release.yml

**Trigger:** Helm version tags (helm-v*.*.\*)

Packages and publishes Helm chart:

- Updates chart version
- Packages chart
- Pushes to OCI registry
- Creates GitHub release with installation instructions

**Usage:**

```bash
git tag helm-v1.0.0
git push origin helm-v1.0.0
```

**Output:**

- `oci://ghcr.io/3-istor/charts/arcl-cmp:1.0.0`
- GitHub release with `.tgz` artifact

## Release Process

### Full Release (Docker Images + Helm Chart)

1. **Prepare release:**

   ```bash
   # Update version in files if needed
   # Commit changes
   git add .
   git commit -m "chore: prepare release v1.0.0"
   git push
   ```

2. **Create Docker image tag:**

   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```

3. **Wait for images to build** (check Actions tab)

4. **Create Helm chart tag:**

   ```bash
   git tag helm-v1.0.0
   git push origin helm-v1.0.0
   ```

5. **Deploy to cluster:**
   ```bash
   helm upgrade --install arcl-cmp oci://ghcr.io/3-istor/charts/arcl-cmp \
     --version 1.0.0 \
     --set image.backend.tag=v1.0.0 \
     --set image.frontend.tag=v1.0.0 \
     --values values-secrets.yaml \
     --namespace arcl-cmp
   ```

### Hotfix Release

For urgent fixes without Helm chart changes:

```bash
git tag v1.0.1
git push origin v1.0.1

# Deploy with new images
helm upgrade arcl-cmp oci://ghcr.io/3-istor/charts/arcl-cmp \
  --version 1.0.0 \
  --set image.backend.tag=v1.0.1 \
  --set image.frontend.tag=v1.0.1 \
  --reuse-values
```

### Helm Chart Only Update

For configuration or template changes:

```bash
git tag helm-v1.0.1
git push origin helm-v1.0.1

# Deploy with existing images
helm upgrade arcl-cmp oci://ghcr.io/3-istor/charts/arcl-cmp \
  --version 1.0.1 \
  --reuse-values
```

## Secrets Required

Configure in GitHub repository settings (Settings → Secrets and variables → Actions):

- `GITHUB_TOKEN` - Automatically provided by GitHub Actions

No additional secrets needed! The workflows use GitHub's built-in authentication.

## Permissions

The workflows require these permissions (already configured in workflow files):

- `contents: write` - Create releases
- `packages: write` - Push to GitHub Container Registry

## Troubleshooting

### Build fails with "permission denied"

Ensure GitHub Actions has write access to packages:

1. Go to repository Settings → Actions → General
2. Under "Workflow permissions", select "Read and write permissions"

### Image push fails

Check that packages are public or you have proper authentication:

1. Go to package settings
2. Change visibility to public if needed
3. Or configure imagePullSecrets in Helm values

### Helm chart push fails

Ensure OCI registry support is enabled and you're using Helm 3.8+.

## Local Testing

Test workflows locally with [act](https://github.com/nektos/act):

```bash
# Install act
brew install act  # macOS
# or download from GitHub releases

# Test build workflow
act push -j build-backend

# Test with specific tag
act push --eventpath test-event.json
```

Example `test-event.json`:

```json
{
  "ref": "refs/tags/v1.0.0"
}
```
