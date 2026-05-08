"""Entry point: ``python -m app.mcp_server``. Runs the stdio MCP server.

Initializes the same SQLite DB + wiki git repo + FTS index that the Flask
app touches, then takes over stdio for the MCP protocol. Logs go to stderr
so they don't corrupt the JSON-RPC stream on stdout.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from mcp.server.stdio import stdio_server

from app.db.sqlite import init_db
from app.llm.agents._session import seen_doc_paths
from app.mcp_server.server import build_server
from app.utils.logging import setup_logging
from app.wiki.git import ensure_wiki_repo
from app.wiki.search import bootstrap_index_if_empty


async def _serve() -> None:
    init_db()
    ensure_wiki_repo()
    bootstrap_index_if_empty()
    seen_doc_paths.set(set())

    server = build_server()
    init_options = server.create_initialization_options()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, init_options)


def main() -> None:
    setup_logging()
    logging.getLogger().handlers[0].setStream(sys.stderr)
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
