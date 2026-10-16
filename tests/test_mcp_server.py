"""MCP discovery and invocation through the official in-memory client."""

import asyncio

from mcp import Client

from opspilot.mcp_server import create_server


class FakeTools:
    def query_metrics(self, args):
        return {"source_id": f"prom:{args.metric}:{args.service}", "series": []}

    def query_logs(self, args):
        return {"source_id": f"loki:{args.kind}:{args.service}", "entries": []}

    def get_recent_deploys(self, args):
        return {"source_id": f"deploys:{args.service}", "deploys": []}

    def search_runbooks(self, args):
        return {"source_id": "knowledge:runbooks", "results": []}

    def get_similar_incidents(self, args):
        return {"source_id": "knowledge:incidents", "results": []}

    def get_backup_jobs(self, args):
        return {"source_id": "backup:jobs", "jobs": []}


def test_read_only_tool_inventory_and_call():
    async def exercise():
        async with Client(create_server(FakeTools())) as client:
            listed = await client.list_tools()
            names = {tool.name for tool in listed.tools}
            assert names == {
                "query_metrics", "query_logs", "get_recent_deploys", "search_runbooks",
                "get_similar_incidents", "get_backup_jobs",
            }
            result = await client.call_tool("query_metrics", {
                "metric": "error_rate", "service": "payments", "range_minutes": 10
            })
            assert not result.is_error
            assert "prom:error_rate:payments" in str(result.content)
            invalid = await client.call_tool("query_metrics", {
                "metric": "arbitrary_promql", "service": "payments"
            })
            assert invalid.is_error

    asyncio.run(exercise())
