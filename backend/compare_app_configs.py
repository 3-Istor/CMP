#!/usr/bin/env python3
"""
Compare current frontend data vs required data.

Shows exactly what's missing and what needs to be added.
"""

import json

# What the frontend currently sends (INCOMPLETE)
CURRENT_FRONTEND_DATA = {
    "name": "my-app",
    "template_id": "k3s-gitops-app",
    "provider_type": "kubernetes",
    "project_id": None,  # ❌ Not set by frontend
    "app_config": {
        "template_repo_name": "template-html-css",
        "app_type": "static",
        "github_owner": "3-Istor",
        # ❌ Missing: project_name
        # ❌ Missing: github_installation_id
    }
}

# What the frontend SHOULD send (COMPLETE)
REQUIRED_FRONTEND_DATA = {
    "name": "my-app",
    "template_id": "k3s-gitops-app",
    "provider_type": "kubernetes",
    "project_id": "project-alpha",  # ✅ Should be set
    "app_config": {
        "template_repo_name": "template-html-css",
        "app_type": "static",
        "github_owner": "3-Istor",
        "project_name": "project-alpha",  # ✅ MUST ADD
        "github_installation_id": 12345678,  # ✅ MUST ADD
        "replica_count": 2,  # Optional
        "sso_protected": False,  # Optional
    }
}


def print_comparison():
    """Print side-by-side comparison."""
    print("=" * 80)
    print("📊 Frontend Data Comparison")
    print("=" * 80)

    print("\n❌ CURRENT (Incomplete - Causes Error):")
    print("-" * 80)
    print(json.dumps(CURRENT_FRONTEND_DATA, indent=2))

    print("\n✅ REQUIRED (Complete - Works):")
    print("-" * 80)
    print(json.dumps(REQUIRED_FRONTEND_DATA, indent=2))

    print("\n🔍 DIFFERENCE:")
    print("-" * 80)

    current_config = CURRENT_FRONTEND_DATA["app_config"]
    required_config = REQUIRED_FRONTEND_DATA["app_config"]

    # Find missing fields
    missing_in_app_config = []
    for key in required_config:
        if key not in current_config:
            missing_in_app_config.append(f"  • app_config.{key} = {required_config[key]}")

    # Check project_id
    if not CURRENT_FRONTEND_DATA["project_id"]:
        print("❌ project_id: None → Should be: 'project-alpha'")

    if missing_in_app_config:
        print("❌ Missing fields in app_config:")
        for field in missing_in_app_config:
            print(field)

    print("\n📋 SUMMARY:")
    print("-" * 80)
    print("The frontend needs to add:")
    print("  1. Set project_id at deployment level")
    print("  2. Add project_name to app_config")
    print("  3. Add github_installation_id to app_config (from user profile)")

    print("\n💡 WHERE TO GET THE DATA:")
    print("-" * 80)
    print("  • project_name: From project selector in UI")
    print("  • github_installation_id: From user.github_installation_id")
    print("    - User must link GitHub account first")
    print("    - Fetched from Keycloak user attributes")
    print("    - Stored when user completes GitHub OAuth flow")

    print("\n🔧 FRONTEND CODE EXAMPLE:")
    print("-" * 80)
    print("""
// In CreateDeploymentModal.tsx
async function handleSubmit(e: React.FormEvent) {
  e.preventDefault();

  // 1. Check GitHub link (for k3s-gitops-app)
  if (template.id === "k3s-gitops-app") {
    if (!user.github_installation_id) {
      toast.error("Please link your GitHub account first");
      navigate("/account");
      return;
    }
  }

  // 2. Build complete app_config
  const app_config = {
    template_repo_name: formData.template_repo_name,
    app_type: formData.app_type || "static",
    github_owner: formData.github_owner || "3-Istor",

    // ⚠️ ADD THESE:
    project_name: formData.project_name || "default",
    github_installation_id: user.github_installation_id,
  };

  // 3. Create deployment
  const response = await fetch("/api/deployments", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: formData.name,
      template_id: template.id,
      provider_type: "kubernetes",
      project_id: formData.project_name || "default",  // ⚠️ ADD THIS
      app_config: app_config,
    }),
  });
}
""")

    print("\n📚 DOCUMENTATION:")
    print("-" * 80)
    print("  • Full guide: DEPLOYMENT_API_REQUIREMENTS.md")
    print("  • Backend fix: TERRAFORM_VARIABLES_FIX_SUMMARY.md")
    print("  • Testing: backend/test_deployment_complete.py")


if __name__ == "__main__":
    print_comparison()
