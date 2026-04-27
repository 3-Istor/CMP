"""
Discord Alerting Service

Sends formatted alerts to Discord via webhook for infrastructure health changes.
"""

import logging
from datetime import datetime
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


async def send_app_alert(
    app_name: str,
    previous_state: str,
    new_state: str,
    details: str = "",
) -> bool:
    """
    Send an alert when an application's health state changes.

    Args:
        app_name: Name of the deployment
        previous_state: Previous health state (e.g., "healthy", "degraded")
        new_state: New health state
        details: Additional details about the state change

    Returns:
        True if alert was sent successfully, False otherwise
    """
    if not settings.DISCORD_WEBHOOK_URL:
        logger.debug("Discord webhook not configured, skipping alert")
        return False

    # Determine color based on new state
    if new_state.lower() in ["healthy", "running"]:
        color = 0x00FF00  # Green
        emoji = "✅"
    elif new_state.lower() in ["degraded"]:
        color = 0xFFA500  # Orange
        emoji = "⚠️"
    elif new_state.lower() in ["down", "failed"]:
        color = 0xFF0000  # Red
        emoji = "🔴"
    else:
        color = 0x808080  # Gray
        emoji = "❓"

    embed = {
        "title": f"{emoji} Application Health Alert",
        "description": f"**{app_name}** health status changed",
        "color": color,
        "fields": [
            {
                "name": "Previous State",
                "value": previous_state.upper(),
                "inline": True
            },
            {
                "name": "New State",
                "value": new_state.upper(),
                "inline": True
            }
        ],
        "timestamp": datetime.utcnow().isoformat(),
        "footer": {
            "text": "CMP Health Monitor"
        }
    }

    if details:
        embed["fields"].append({
            "name": "Details",
            "value": details,
            "inline": False
        })

    payload = {
        "embeds": [embed]
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                settings.DISCORD_WEBHOOK_URL,
                json=payload
            )
            response.raise_for_status()
            logger.info(
                "Discord alert sent for %s: %s -> %s",
                app_name,
                previous_state,
                new_state
            )
            return True
    except Exception as exc:
        logger.error("Failed to send Discord alert: %s", exc)
        return False


async def send_infra_alert(
    component_name: str,
    previous_state: str,
    new_state: str,
    component_type: str = "infrastructure"
) -> bool:
    """
    Send an alert when infrastructure component state changes.

    Args:
        component_name: Name of the component (e.g., "pae-node-1", "vpn-gateway")
        previous_state: Previous state (e.g., "up", "active", "running")
        new_state: New state (e.g., "down", "inactive", "stopped")
        component_type: Type of component (e.g., "hypervisor", "vpn")

    Returns:
        True if alert was sent successfully, False otherwise
    """
    if not settings.DISCORD_WEBHOOK_URL:
        logger.debug("Discord webhook not configured, skipping alert")
        return False

    # Determine color based on new state
    healthy_states = ["up", "active", "running", "enabled"]
    unhealthy_states = ["down", "inactive", "stopped", "disabled", "error"]

    if new_state.lower() in healthy_states:
        color = 0x00FF00  # Green
        emoji = "✅"
    elif new_state.lower() in unhealthy_states:
        color = 0xFF0000  # Red
        emoji = "🔴"
    else:
        color = 0xFFA500  # Orange
        emoji = "⚠️"

    embed = {
        "title": f"{emoji} Infrastructure Alert",
        "description": f"**{component_name}** ({component_type}) state changed",
        "color": color,
        "fields": [
            {
                "name": "Previous State",
                "value": previous_state.upper(),
                "inline": True
            },
            {
                "name": "New State",
                "value": new_state.upper(),
                "inline": True
            }
        ],
        "timestamp": datetime.utcnow().isoformat(),
        "footer": {
            "text": "CMP Infrastructure Monitor"
        }
    }

    payload = {
        "embeds": [embed]
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                settings.DISCORD_WEBHOOK_URL,
                json=payload
            )
            response.raise_for_status()
            logger.info(
                "Discord alert sent for %s: %s -> %s",
                component_name,
                previous_state,
                new_state
            )
            return True
    except Exception as exc:
        logger.error("Failed to send Discord infra alert: %s", exc)
        return False
