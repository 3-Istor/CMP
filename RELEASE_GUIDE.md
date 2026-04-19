# ARCL CMP Release Guide

## Quick Release

Use the automated release script:

```bash
./scripts/release.sh 1.0.0
```

This will:

1. Create and push `v1.0.0` tag (triggers Docker image build)
2. Wait 10 seconds
3. Create and push `helm-v1.0.0` tag (triggers Helm chart release)

## Manual Release

If you prefer to do it manually:

### Step 1: Build and Push Docker Images

```bash
# Create version tag
git tag -a v1.0.0 -m "Release v1.0.0"

# Push to GitHub (triggers build-and-push.yml workflow)
git push origin v1.0.0
```

This triggers the `build-and-push.yml` workflow which:

- Builds multi-arch Docker images (amd64, arm64)
- Pushes to `ghcr.io/3-istor/cmp-backend:v1.0.0`
- Pushes to `ghcr.io/3-istor/cmp-frontend:v1.0.0`
- Creates GitHub release

### Step 2: Release Helm Chart

```bash
# Create Helm chart tag
git tag -a helm-v1.0.0 -m "Helm chart release v1.0.0"

# Push to GitHub (triggers helm-release.yml workflow)
git push origin helm-v1.0.0
```

This triggers the `helm-release.yml` workflow which:

- Updates chart version to 1.0.0
- Packages Helm chart
- Pushes to `oci://ghcr.io/3-istor/charts/arcl-cmp`
- Creates GitHub release with install instructions

## Monitoring

### Check Workflow Status

Visit: https://github.com/3-Istor/CMP/actions

You should see:

- ✅ Build and Push Docker Images
- ✅ Helm Chart Release

### Check Packages

Docker Images: https://github.com/orgs/3-Istor/packages?repo_name=CMP

Helm Charts: https://github.com/3-Istor/CMP/pkgs/container/charts%2Farcl-cmp

Note: Packages may take 2-5 minutes to appear after workflow completes.

## Deploying a Release

### Using Docker Images

```bash
# Pull specific version
docker pull ghcr.io/3-istor/cmp-backend:v1.0.0
docker pull ghcr.io/3-istor/cmp-frontend:v1.0.0

# Or use docker-compose with specific tags
docker-compose up -d
```

### Using Helm Chart

```bash
# Install from OCI registry
helm install arcl-cmp oci://ghcr.io/3-istor/charts/arcl-cmp \
  --version 1.0.0 \
  --namespace arcl-cmp \
  --create-namespace \
  --values values-secrets.yaml

# Upgrade to new version
helm upgrade arcl-cmp oci://ghcr.io/3-istor/charts/arcl-cmp \
  --version 1.0.1 \
  --reuse-values
```

## Version Numbering

Follow Semantic Versioning (semver):

- **Major** (1.0.0): Breaking changes
- **Minor** (0.1.0): New features, backwards compatible
- **Patch** (0.0.1): Bug fixes, backwards compatible

Examples:

- `v1.0.0` - First stable release
- `v1.1.0` - Added new feature
- `v1.1.1` - Fixed bug in v1.1.0
- `v2.0.0` - Breaking API changes

## Troubleshooting

### Workflow Not Triggering

**Problem**: Created tag locally but workflow didn't run

**Solution**: You must PUSH the tag to GitHub

```bash
# Push specific tag
git push origin v1.0.0

# Or push all tags
git push origin --tags
```

### Build Failed

**Problem**: Docker build or Helm package failed

**Solution**: Check the workflow logs

1. Go to https://github.com/3-Istor/CMP/actions
2. Click on the failed workflow
3. Check the error logs
4. Fix the issue
5. Delete the tag and recreate:

```bash
# Delete local tag
git tag -d v1.0.0

# Delete remote tag
git push origin :refs/tags/v1.0.0

# Fix the issue, commit, then recreate tag
git tag -a v1.0.0 -m "Release v1.0.0"
git push origin v1.0.0
```

### Package Not Appearing

**Problem**: Workflow succeeded but package not visible

**Solution**:

1. Wait 2-5 minutes for package to be indexed
2. Check package visibility settings:
   - Go to package settings
   - Ensure visibility is set to "Public"
3. Check if you have the right permissions

### Image Pull Failed

**Problem**: Cannot pull image from ghcr.io

**Solution**: Authenticate with GitHub Container Registry

```bash
# Create a Personal Access Token (PAT) with read:packages scope
# Then login:
echo $GITHUB_TOKEN | docker login ghcr.io -u USERNAME --password-stdin

# Or for Kubernetes, create imagePullSecret:
kubectl create secret docker-registry ghcr-secret \
  --docker-server=ghcr.io \
  --docker-username=USERNAME \
  --docker-password=$GITHUB_TOKEN \
  --namespace=arcl-cmp
```

## CI/CD Workflows

### test.yml

**Trigger**: Push to main/develop, Pull Requests

**Actions**:

- Lint backend (black, isort, pylint)
- Lint frontend (eslint)
- Build Docker images (validation only)

### build-and-push.yml

**Trigger**: Push tags matching `v*.*.*`

**Actions**:

- Build multi-arch Docker images
- Push to ghcr.io
- Create GitHub release

### helm-release.yml

**Trigger**: Push tags matching `helm-v*.*.*`

**Actions**:

- Update chart version
- Package Helm chart
- Push to OCI registry
- Create GitHub release

## Best Practices

1. **Always test locally first**

   ```bash
   docker-compose up -d
   ```

2. **Run linting before committing**

   ```bash
   cd backend && poetry run black app/ && poetry run isort app/
   cd frontend && npm run lint -- --fix
   ```

3. **Use the release script for consistency**

   ```bash
   ./scripts/release.sh 1.0.0
   ```

4. **Tag after merging to main**
   - Develop on feature branches
   - Merge to main via PR
   - Tag main branch for releases

5. **Write meaningful release notes**
   - GitHub releases are auto-generated
   - Edit them to add highlights and breaking changes

6. **Keep Helm chart and Docker versions in sync**
   - Use same version number for both
   - The release script handles this automatically

## Support

For issues:

- GitHub Issues: https://github.com/3-Istor/CMP/issues
- GitHub Discussions: https://github.com/3-Istor/CMP/discussions
