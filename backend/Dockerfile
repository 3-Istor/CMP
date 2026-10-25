# Multi-stage build for Python FastAPI backend
FROM python:3.14-slim AS builder

WORKDIR /app

# Install system dependencies including build tools
RUN apt-get update && apt-get install -y \
    curl \
    git \
    unzip \
    gcc \
    g++ \
    make \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Terraform
RUN curl -fsSL https://releases.hashicorp.com/terraform/1.11.4/terraform_1.11.4_linux_amd64.zip -o terraform.zip \
    && unzip terraform.zip \
    && mv terraform /usr/local/bin/ \
    && rm terraform.zip

# Install Poetry
RUN pip install poetry==1.8.0

# Copy dependency files
COPY pyproject.toml poetry.lock ./

# Install dependencies
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --no-root --only main

# Final stage
FROM python:3.14-slim

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    git \
    sqlite3 \
    curl \
    unzip \
    gnupg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Cloud CLIs used by cluster up/down (pinned)
ARG AWSCLI_VERSION=2.34.0
ARG KUBECTL_VERSION=v1.35.9
ARG GCLOUD_VERSION=590.0.0-0
RUN curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64-${AWSCLI_VERSION}.zip" -o /tmp/awscli.zip \
    && unzip -q /tmp/awscli.zip -d /tmp && /tmp/aws/install && rm -rf /tmp/aws /tmp/awscli.zip \
    && curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" -o /usr/local/bin/kubectl \
    && chmod +x /usr/local/bin/kubectl \
    && curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" > /etc/apt/sources.list.d/google-cloud-sdk.list \
    && apt-get update && apt-get install -y \
    google-cloud-cli=${GCLOUD_VERSION} \
    google-cloud-cli-gke-gcloud-auth-plugin=${GCLOUD_VERSION} \
    && rm -rf /var/lib/apt/lists/*

# Copy Terraform from builder
COPY --from=builder /usr/local/bin/terraform /usr/local/bin/terraform

# Copy Python packages from builder
COPY --from=builder /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY . .

# Make entrypoint executable
RUN chmod +x /app/docker-entrypoint.sh

# Cluster terraform roots (from `make cloud` / CI checkout), providers pre-cached.
# World-writable: the pod runs as uid 1000 and terraform writes .terraform/ state there.
RUN mv /app/cloud-src/terraform/public /app/cloud && rm -rf /app/cloud-src \
    && find /app/cloud \( -name .runtime -o -name '*.tfvars' \) -prune -exec rm -rf {} + \
    && mkdir /app/cloud/.plugin-cache \
    && for d in aws/EKS gcp/GKE; do \
        (cd /app/cloud/$d && TF_PLUGIN_CACHE_DIR=/app/cloud/.plugin-cache terraform init -backend=false -input=false); \
    done \
    && chmod -R a+rwX /app/cloud

# Create data directories
RUN mkdir -p /app/data/templates /app/data/terraform_states

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/health')"

# Run migrations and start server
ENTRYPOINT ["/app/docker-entrypoint.sh"]
