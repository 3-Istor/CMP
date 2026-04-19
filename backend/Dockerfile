# Multi-stage build for Python FastAPI backend
FROM python:3.12-slim AS builder

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
RUN curl -fsSL https://releases.hashicorp.com/terraform/1.7.5/terraform_1.7.5_linux_amd64.zip -o terraform.zip \
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
FROM python:3.12-slim

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    git \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Copy Terraform from builder
COPY --from=builder /usr/local/bin/terraform /usr/local/bin/terraform

# Copy Python packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY . .

# Make entrypoint executable
RUN chmod +x /app/docker-entrypoint.sh

# Create data directories
RUN mkdir -p /app/data/templates /app/data/terraform_states

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/health')"

# Run migrations and start server
ENTRYPOINT ["/app/docker-entrypoint.sh"]
