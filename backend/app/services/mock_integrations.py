from datetime import datetime, timezone


class MockSocAdapter:
    """Creates internal drafts only; it never contacts an external SOC system."""

    def create_draft(self, action: str, resource: str, reason: str) -> dict[str, str]:
        if action not in {
            "external.ticket.draft",
            "external.incident_report.draft",
            "external.detection_rule.draft",
        }:
            raise ValueError(f"No draft adapter is registered for action: {action}")
        return {
            "adapter": "mock",
            "status": "draft",
            "action": action,
            "target_resource": resource,
            "proposed_content": reason,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "external_system_contacted": "false",
        }
