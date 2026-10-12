"""Optional local stdio MCP adapter. No unauthenticated HTTP listener."""
from mcp.server.fastmcp import FastMCP
from ntfy_tools import register_ntfy

mcp=FastMCP('ntfy inbox')
register_ntfy(mcp,lambda tool:None)
if __name__=='__main__':mcp.run(transport='stdio')
