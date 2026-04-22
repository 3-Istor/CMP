#!/usr/bin/env python3
"""
Quick test script for monitoring endpoints.

Usage:
    python test_monitoring.py
"""

import asyncio
import sys

from app.services import monitoring_service


async def test_global_health():
    """Test global infrastructure health check."""
    print("=" * 60)
    print("Testing Global Infrastructure Health")
    print("=" * 60)

    try:
        health = await monitoring_service.get_global_health()

        print("\n✓ OpenStack VPN:")
        if health.openstack_vpn:
            print(f"  Name: {health.openstack_vpn.name}")
            print(f"  Status: {health.openstack_vpn.status}")
            print(f"  IP: {health.openstack_vpn.ip}")
        else:
            print("  Not found")

        print("\n✓ AWS VPNs:")
        if health.aws_vpns:
            for vpn in health.aws_vpns:
                print(f"  - {vpn.name}: {vpn.status} ({vpn.ip})")
        else:
            print("  None found")

        print("\n✓ OpenStack Hypervisors:")
        if health.openstack_hypervisors:
            for hv in health.openstack_hypervisors:
                print(f"  - {hv.name}: {hv.state}/{hv.status}")
        else:
            print("  None found")

        print("\n✅ Global health check passed!")
        return True

    except Exception as exc:
        print(f"\n❌ Global health check failed: {exc}")
        import traceback
        traceback.print_exc()
        return False


async def test_app_health(deployment_name: str):
    """Test application-specific health check."""
    print("\n" + "=" * 60)
    print(f"Testing Application Health: {deployment_name}")
    print("=" * 60)

    try:
        health = await monitoring_service.get_app_health(deployment_name)

        print(f"\n✓ Overall Status: {health.status.upper()}")

        if health.aws_frontend:
            print("\n✓ AWS Frontend:")
            print(f"  ASG: {health.aws_frontend['asg_name']}")
            print(f"  Desired Capacity: {health.aws_frontend['desired_capacity']}")
            print(f"  Healthy: {health.aws_frontend['healthy_count']} / {health.aws_frontend['total_count']}")

            if health.aws_frontend['instances']:
                print("  Instances:")
                for inst in health.aws_frontend['instances']:
                    print(f"    - {inst['instance_id']}: {inst['state']} / {inst['health']} ({inst['private_ip']})")
        else:
            print("\n✓ AWS Frontend: Not found")

        if health.openstack_backend:
            print("\n✓ OpenStack Backend:")
            print(f"  Healthy: {health.openstack_backend['healthy_count']} / {health.openstack_backend['total_count']}")

            if health.openstack_backend['servers']:
                print("  Servers:")
                for server in health.openstack_backend['servers']:
                    print(f"    - {server['instance_id'][:8]}...: {server['state']} / {server['health']} ({server['private_ip']})")
        else:
            print("\n✓ OpenStack Backend: Not found")

        print("\n✅ Application health check passed!")
        return True

    except Exception as exc:
        print(f"\n❌ Application health check failed: {exc}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run all tests."""
    print("\n🔍 ARCL CMP Monitoring Service Test Suite\n")

    # Test global health
    global_ok = await test_global_health()

    # Test app health (if deployment name provided)
    app_ok = True
    if len(sys.argv) > 1:
        deployment_name = sys.argv[1]
        app_ok = await test_app_health(deployment_name)
    else:
        print("\n💡 Tip: Run with deployment name to test app health:")
        print("   python test_monitoring.py my-wordpress-app")

    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    print(f"Global Health: {'✅ PASS' if global_ok else '❌ FAIL'}")
    if len(sys.argv) > 1:
        print(f"App Health:    {'✅ PASS' if app_ok else '❌ FAIL'}")
    print()

    return 0 if (global_ok and app_ok) else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
