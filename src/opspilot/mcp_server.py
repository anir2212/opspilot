"""Read-only MCP façade over the same validated investigation tools as the agent."""

from __future__ import annotations

from mcp.server import MCPServer

from opspilot.tools import (
    BackupQuery,
    InvestigationTools,
    LogQuery,
    MetricQuery,
    SearchQuery,
    ServiceWindow,
)


def create_server(tools: InvestigationTools | None = None) -> MCPServer:
    investigation = tools or InvestigationTools()
    server = MCPServer(
        "OpsPilot",
        instructions=(
            "Local incident investigation only. "
            "These tools cannot approve or execute changes."
        ),
    )

    @server.tool()
    def query_metrics(
        metric: str, service: str, range_minutes: int = 10
    ) -> dict:
        """Get one allow-listed Prometheus metric for a demo service."""
        return investigation.query_metrics(
            MetricQuery.model_validate({"metric": metric, "service": service,
                                        "range_minutes": range_minutes})
        )

    @server.tool()
    def query_logs(kind: str, service: str, range_minutes: int = 10) -> dict:
        """Get one allow-listed Loki log class for a demo service."""
        return investigation.query_logs(LogQuery.model_validate(
            {"kind": kind, "service": service, "range_minutes": range_minutes}
        ))

    @server.tool()
    def get_recent_deploys(service: str, window_minutes: int = 30) -> dict:
        """Read recent local deployment records for a service."""
        return investigation.get_recent_deploys(
            ServiceWindow.model_validate({"service": service, "window_minutes": window_minutes})
        )

    @server.tool()
    def search_runbooks(query: str, k: int = 5) -> dict:
        """Search local authored runbooks and return citation IDs."""
        return investigation.search_runbooks(SearchQuery(query=query, k=k))

    @server.tool()
    def get_similar_incidents(text: str, k: int = 5) -> dict:
        """Find synthetic historical incidents similar to the question."""
        return investigation.get_similar_incidents(SearchQuery(query=text, k=k))

    @server.tool()
    def get_backup_jobs(status: str | None = None, window_minutes: int = 60) -> dict:
        """Read recent backup-job records; status is optional and validated."""
        return investigation.get_backup_jobs(
            BackupQuery.model_validate({"status": status, "window_minutes": window_minutes})
        )

    return server


def main() -> None:
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
