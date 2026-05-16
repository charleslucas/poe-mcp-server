"""Minimal mcp_server_utils shim — replaces the buildstuff version."""
from mcp.server.stdio import stdio_server
import anyio


def run_server(app, **_kwargs):
    async def _main():
        async with stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream,
                write_stream,
                app.create_initialization_options(),
                raise_exceptions=False,
            )

    anyio.run(_main)
