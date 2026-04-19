#!/bin/bash
set -e

# ARCL CMP k3s Deployment Script

NAMESPACE="arcl-cmp"
RELEASE_NAME="arcl-cmp"
CHART_PATH="./helm/arcl-cmp"

echo "🚀 ARCL CMP k3s Deployment Script"
echo "=================================="

# Check prerequisites
command -v kubectl >/dev/null 2>&1 || { echo "❌ kubectl is required but not installed."; exit 1; }
command -v helm >/dev/null 2>&1 || { echo "❌ helm is required but not installed."; exit 1; }

# Check if kubectl can connect to cluster
if ! kubectl cluster-info >/dev/null 2>&1; then
    echo "❌ Cannot connect to Kubernetes cluster. Is k3s running?"
    exit 1
fi

echo "✅ Prerequisites check passed"

# Create namespace if it doesn't exist
if ! kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
    echo "📦 Creating namespace: $NAMESPACE"
    kubectl create namespace "$NAMESPACE"
else
    echo "✅ Namespace $NAMESPACE already exists"
fi

# Check if values-secrets.yaml exists
if [ ! -f "values-secrets.yaml" ]; then
    echo "⚠️  values-secrets.yaml not found!"
    echo "Creating template values-secrets.yaml..."
    cat > values-secrets.yaml <<EOF
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
    - host: arcl-cmp.local
      paths:
        - path: /api
          pathType: Prefix
          service: backend
        - path: /
          pathType: Prefix
          service: frontend
EOF
    echo "❌ Please edit values-secrets.yaml with your credentials and run again."
    exit 1
fi

echo "✅ Found values-secrets.yaml"

# Check if release exists
if helm list -n "$NAMESPACE" | grep -q "$RELEASE_NAME"; then
    echo "🔄 Upgrading existing release..."
    helm upgrade "$RELEASE_NAME" "$CHART_PATH" \
        --namespace "$NAMESPACE" \
        --values values-secrets.yaml \
        --wait \
        --timeout 5m
    echo "✅ Upgrade completed"
else
    echo "📦 Installing new release..."
    helm install "$RELEASE_NAME" "$CHART_PATH" \
        --namespace "$NAMESPACE" \
        --values values-secrets.yaml \
        --wait \
        --timeout 5m
    echo "✅ Installation completed"
fi

# Wait for pods to be ready
echo "⏳ Waiting for pods to be ready..."
kubectl wait --for=condition=ready pod \
    --selector=app.kubernetes.io/instance="$RELEASE_NAME" \
    --namespace="$NAMESPACE" \
    --timeout=300s

# Display status
echo ""
echo "📊 Deployment Status"
echo "===================="
kubectl get pods -n "$NAMESPACE"
echo ""
kubectl get svc -n "$NAMESPACE"
echo ""
kubectl get ingress -n "$NAMESPACE"

# Get ingress URL
INGRESS_HOST=$(kubectl get ingress -n "$NAMESPACE" -o jsonpath='{.items[0].spec.rules[0].host}')
if [ -n "$INGRESS_HOST" ]; then
    echo ""
    echo "🌐 Application URL: https://$INGRESS_HOST"
    echo ""
    echo "For local testing, add to /etc/hosts:"
    echo "127.0.0.1 $INGRESS_HOST"
fi

echo ""
echo "✅ Deployment completed successfully!"
echo ""
echo "Useful commands:"
echo "  View logs (backend):  kubectl logs -f deployment/$RELEASE_NAME-backend -n $NAMESPACE"
echo "  View logs (frontend): kubectl logs -f deployment/$RELEASE_NAME-frontend -n $NAMESPACE"
echo "  Port forward:         kubectl port-forward svc/$RELEASE_NAME-frontend 3000:3000 -n $NAMESPACE"
echo "  Uninstall:            helm uninstall $RELEASE_NAME -n $NAMESPACE"
