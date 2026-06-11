#!/usr/bin/env python3
"""
Environment Variables Validation Script

Checks that all required environment variables for Terraform templates are set.
Run this before deploying to catch configuration issues early.
"""

from app.core.config import settings


def validate_env():
    """Validate all required environment variables."""
    print("🔍 Validating Environment Variables for CMP Backend\n")
    print("=" * 80)

    all_valid = True

    # Core Infrastructure Variables
    print("\n📦 Core Infrastructure")
    print("-" * 80)

    core_vars = [
        ("AWS_ACCESS_KEY_ID", settings.AWS_ACCESS_KEY_ID, True),
        ("AWS_SECRET_ACCESS_KEY", settings.AWS_SECRET_ACCESS_KEY, True),
        ("AWS_DEFAULT_REGION", settings.AWS_DEFAULT_REGION, True),
        ("OS_AUTH_URL", settings.OS_AUTH_URL, True),
        ("OS_USERNAME", settings.OS_USERNAME, True),
        ("OS_PASSWORD", settings.OS_PASSWORD, True),
        ("OS_PROJECT_NAME", settings.OS_PROJECT_NAME, True),
    ]

    for name, value, required in core_vars:
        status = check_var(name, value, required)
        if not status and required:
            all_valid = False

    # Cloudflare Variables (for k3s-gitops-app template)
    print("\n🌐 Cloudflare DNS (k3s-gitops-app template)")
    print("-" * 80)

    cloudflare_vars = [
        ("CLOUDFLARE_API_TOKEN", settings.CLOUDFLARE_API_TOKEN, True),
        ("CLOUDFLARE_ZONE_ID", settings.CLOUDFLARE_ZONE_ID, True),
        ("CLOUDFLARE_ACCOUNT_ID", settings.CLOUDFLARE_ACCOUNT_ID, True),
    ]

    for name, value, required in cloudflare_vars:
        status = check_var(name, value, required)
        if not status and required:
            all_valid = False

    # Keycloak Variables (for k3s-gitops-app template)
    print("\n🔑 Keycloak SSO (k3s-gitops-app template)")
    print("-" * 80)

    keycloak_vars = [
        ("KEYCLOAK_URL", settings.KEYCLOAK_URL, True),
        ("KEYCLOAK_CLIENT_ID", settings.KEYCLOAK_CLIENT_ID, True),
        ("KEYCLOAK_CLIENT_SECRET", settings.KEYCLOAK_CLIENT_SECRET, True),
        ("KEYCLOAK_ADMIN_USERNAME", settings.KEYCLOAK_ADMIN_USERNAME, True),
        ("KEYCLOAK_ADMIN_PASSWORD", settings.KEYCLOAK_ADMIN_PASSWORD, True),
    ]

    for name, value, required in keycloak_vars:
        status = check_var(name, value, required)
        if not status and required:
            all_valid = False

    # Vault Variables (for k3s-gitops-app template)
    print("\n🔒 HashiCorp Vault (k3s-gitops-app template)")
    print("-" * 80)

    vault_vars = [
        ("VAULT_URL", settings.VAULT_URL, True),
        ("VAULT_TOKEN", settings.VAULT_TOKEN, True),
    ]

    for name, value, required in vault_vars:
        status = check_var(name, value, required)
        if not status and required:
            all_valid = False

    # GitHub Variables
    print("\n🐙 GitHub Integration")
    print("-" * 80)

    github_vars = [
        ("GITHUB_APP_PRIVATE_KEY", settings.GITHUB_APP_PRIVATE_KEY, True),
        ("GITHUB_REGISTRY_TOKEN", settings.GITHUB_REGISTRY_TOKEN, True),
    ]

    for name, value, required in github_vars:
        status = check_var(name, value, required)
        if not status and required:
            all_valid = False

    # S3 Backend (optional)
    print("\n📦 Terraform S3 Backend (optional)")
    print("-" * 80)

    s3_vars = [
        ("TF_BACKEND_S3_ENABLED", str(settings.TF_BACKEND_S3_ENABLED), False),
        ("TF_BACKEND_S3_BUCKET", settings.TF_BACKEND_S3_BUCKET, False),
        ("TF_BACKEND_AWS_REGION", settings.TF_BACKEND_AWS_REGION, False),
    ]

    for name, value, required in s3_vars:
        check_var(name, value, required)

    # Final Summary
    print("\n" + "=" * 80)
    if all_valid:
        print("✅ All required environment variables are configured!")
        print("\n🚀 You can now run deployments safely.")
        return 0
    else:
        print("❌ Some required environment variables are missing!")
        print("\n📝 Update your backend/.env file with the missing values.")
        print("📖 See backend/.env.example for reference.")
        return 1


def check_var(name: str, value: any, required: bool = True) -> bool:
    """Check if a variable is set and display status."""
    if value:
        # Truncate long values for display
        display_value = str(value)
        if len(display_value) > 50:
            display_value = display_value[:47] + "..."

        # Mask sensitive values
        if any(keyword in name.lower() for keyword in ["password", "token", "secret", "key"]):
            if "PRIVATE_KEY" in name:
                display_value = "[PEM KEY - " + str(len(value)) + " chars]"
            else:
                display_value = display_value[:10] + "..." if len(display_value) > 10 else "[SET]"

        print(f"  ✅ {name:40} = {display_value}")
        return True
    else:
        if required:
            print(f"  ❌ {name:40} = NOT SET (REQUIRED)")
            return False
        else:
            print(f"  ⚠️  {name:40} = NOT SET (optional)")
            return True


if __name__ == "__main__":
    import sys
    sys.exit(validate_env())
