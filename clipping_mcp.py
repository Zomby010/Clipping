#!/usr/bin/env python3
"""
clipping_mcp.py — The clipping tools as an MCP server, so Claude Code can run them.

Claude Code starts it by itself from .mcp.json when you open Claude in the
Clipping folder. To check it by hand:

    pip install -r requirements-claude-tools.txt
    python clipping_mcp.py --list

The tools are in clipping/claude_tools.py.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipping import claude_tools as T  # noqa: E402

TOOLS = [
    T.status,
    T.list_my_clips,
    T.list_edits,
    T.upload_list,
    T.clipsai_find_clips,
    T.make_edit,
    T.clipsai_follow_speaker,
    T.export_edits,
    T.postiz_channels,
    T.postiz_send,
]

INSTRUCTIONS = (
    "Tools for the Clipping project: Clipping Studio's clips (My Clips), the Clip Editor's edits (My Edits), "
    "exports to Ready to post and the upload list, ClipsAI (find talk clips, follow the speaker) and Postiz "
    "(post to TikTok, Instagram Reels, YouTube and Facebook). Call status first. postiz_send is a dry run "
    "unless post=true: show the dry run to the user before posting. Times are Africa/Nairobi."
)


def build_server():
    try:
        from mcp.server.mcpserver import MCPServer as Server   # mcp 2.x
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP as Server   # mcp 1.x
        except ImportError:
            sys.exit("The MCP package is missing. Run:  pip install -r requirements-claude-tools.txt")
    server = Server("clipping", instructions=INSTRUCTIONS)
    for fn in TOOLS:
        server.tool()(fn)
    return server


def main():
    if "--list" in sys.argv:
        for fn in TOOLS:
            print(f"{fn.__name__:24} {(fn.__doc__ or '').strip().splitlines()[0]}")
        return
    build_server().run()


if __name__ == "__main__":
    main()
