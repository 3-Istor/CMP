#!/usr/bin/env python3
"""
Verification script for Grafana integration.

Checks that all components are properly integrated and ready for deployment.

Usage:
    poetry run python verify_grafana_integration.py
"""

import sys
from pathlib import Path


def check_file_exists(filepath: Path, description: str) -> bool:
    """Check if a file exists."""
    if filepath.exists():
        print(f"  ✅ {description}: {filepath.name}")
        return True
    else:
        print(f"  ❌ {description}: {filepath.name} NOT FOUND")
        return False


def check_import(module_path: str, description: str) -> bool:
    """Check if a module can be imported."""
    try:
        parts = module_path.split(".")
        module = __import__(module_path)
        for part in parts[1:]:
            module = getattr(module, part)
        print(f"  ✅ {description}: {module_path}")
        return True
    except ImportError as e:
        print(f"  ❌ {description}: {module_path} — {e}")
        return False
    except Exception as e:
        print(f"  ⚠️  {description}: {module_path} — {e}")
        return False


def check_function_exists(module_path: str, function_name: str) -> bool:
    """Check if a function exists in a module."""
    try:
        parts = module_path.split(".")
        module = __import__(module_path)
        for part in parts[1:]:
            module = getattr(module, part)
        if hasattr(module, function_name):
            print(f"  ✅ Function: {function_name}()")
            return True
        else:
            print(f"  ❌ Function: {function_name}() NOT FOUND")
            return False
    except Exception as e:
        print(f"  ❌ Function check failed: {e}")
        return False


def main():
    """Run verification checks."""
    print("\n" + "=" * 80)
    print("GRAFANA INTEGRATION VERIFICATION")
    print("=" * 80)

    all_passed = True

    # ── Check 1: Files Created ────────────────────────────────────────────
    print("\n[1] Checking created files...")
    backend_dir = Path(__file__).parent

    files_to_check = [
        (
            backend_dir / "app" / "services" / "grafana_service.py",
            "Grafana service",
        ),
        (backend_dir / "test_grafana_service.py", "Test suite"),
        (backend_dir / "GRAFANA_INTEGRATION.md", "Full documentation"),
        (backend_dir / "GRAFANA_QUICKSTART.md", "Quick start guide"),
        (
            backend_dir / "GRAFANA_IMPLEMENTATION_SUMMARY.md",
            "Implementation summary",
        ),
        (
            backend_dir / "verify_grafana_integration.py",
            "This verification script",
        ),
    ]

    for filepath, description in files_to_check:
        if not check_file_exists(filepath, description):
            all_passed = False

    # ── Check 2: Python Imports ───────────────────────────────────────────
    print("\n[2] Checking Python imports...")

    imports_to_check = [
        ("app.services.grafana_service", "Grafana service module"),
        ("app.routers.projects", "Projects router"),
        ("app.core.config", "Config module"),
        ("app.main", "Main application"),
    ]

    for module_path, description in imports_to_check:
        if not check_import(module_path, description):
            all_passed = False

    # ── Check 3: Grafana Service Functions ────────────────────────────────
    print("\n[3] Checking Grafana service functions...")

    functions_to_check = [
        "add_user_to_project_org",
        "remove_user_from_project_org",
        "close_http_client",
        "_grafana_org_name",
        "_map_role_to_grafana",
    ]

    for func_name in functions_to_check:
        if not check_function_exists(
            "app.services.grafana_service", func_name
        ):
            all_passed = False

    # ── Check 4: Configuration ────────────────────────────────────────────
    print("\n[4] Checking configuration...")

    try:
        from app.core.config import settings

        if hasattr(settings, "GRAFANA_ADMIN_PASSWORD"):
            print("  ✅ Configuration: GRAFANA_ADMIN_PASSWORD setting exists")
            if settings.GRAFANA_ADMIN_PASSWORD:
                print("  ✅ Configuration: GRAFANA_ADMIN_PASSWORD is set")
            else:
                print(
                    "  ⚠️  Configuration: GRAFANA_ADMIN_PASSWORD is empty (set in .env)"
                )
                print(
                    "     Add to backend/.env: GRAFANA_ADMIN_PASSWORD=your_password"
                )
        else:
            print(
                "  ❌ Configuration: GRAFANA_ADMIN_PASSWORD setting not found"
            )
            all_passed = False
    except Exception as e:
        print(f"  ❌ Configuration check failed: {e}")
        all_passed = False

    # ── Check 5: Integration Points ───────────────────────────────────────
    print("\n[5] Checking integration in projects router...")

    try:
        import inspect

        from app.routers.projects import (
            add_project_member,
            create_project,
            remove_project_member,
        )

        # Check if add_user_to_project_org is imported
        source = inspect.getsource(create_project)
        if "add_user_to_project_org" in source:
            print(
                "  ✅ Integration: create_project() uses add_user_to_project_org"
            )
        else:
            print("  ❌ Integration: create_project() missing Grafana sync")
            all_passed = False

        source = inspect.getsource(add_project_member)
        if "add_user_to_project_org" in source:
            print(
                "  ✅ Integration: add_project_member() uses add_user_to_project_org"
            )
        else:
            print(
                "  ❌ Integration: add_project_member() missing Grafana sync"
            )
            all_passed = False

        source = inspect.getsource(remove_project_member)
        if "remove_user_from_project_org" in source:
            print(
                "  ✅ Integration: remove_project_member() uses remove_user_from_project_org"
            )
        else:
            print(
                "  ❌ Integration: remove_project_member() missing Grafana sync"
            )
            all_passed = False

    except Exception as e:
        print(f"  ❌ Integration check failed: {e}")
        all_passed = False

    # ── Check 6: Lifecycle Cleanup ────────────────────────────────────────
    print("\n[6] Checking application lifecycle...")

    try:
        import inspect

        from app.main import lifespan

        source = inspect.getsource(lifespan)
        if "close_http_client" in source:
            print(
                "  ✅ Lifecycle: Grafana HTTP client cleanup in shutdown handler"
            )
        else:
            print("  ❌ Lifecycle: Missing Grafana HTTP client cleanup")
            all_passed = False

    except Exception as e:
        print(f"  ❌ Lifecycle check failed: {e}")
        all_passed = False

    # ── Check 7: Dependencies ─────────────────────────────────────────────
    print("\n[7] Checking dependencies...")

    try:
        import httpx

        print(f"  ✅ Dependency: httpx {httpx.__version__}")
    except ImportError:
        print("  ❌ Dependency: httpx not installed")
        print("     Run: poetry add httpx")
        all_passed = False

    # ── Summary ───────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    if all_passed:
        print("✅ VERIFICATION PASSED — All checks successful!")
        print("\nNext steps:")
        print("  1. Add GRAFANA_ADMIN_PASSWORD to backend/.env")
        print("  2. Run test suite: poetry run python test_grafana_service.py")
        print("  3. Restart backend: poetry run uvicorn app.main:app --reload")
        print("  4. Test manually: Create project → Check Grafana")
        print("=" * 80)
        return 0
    else:
        print("❌ VERIFICATION FAILED — Some checks did not pass")
        print("\nPlease fix the issues above before deploying.")
        print("=" * 80)
        return 1


if __name__ == "__main__":
    sys.exit(main())
