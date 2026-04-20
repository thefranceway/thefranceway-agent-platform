"""
AD4M Tool Layer — wraps AD4M's local GraphQL API for use inside agent tool loops.

AD4M executor must be running at localhost:4000 (or AD4M_GQL_URL env override).
Start it with: ~/bin/ad4m-executor run --gql-port 4000

Tools exposed:
  - ad4m_agent_status        → get local agent DID + lock state
  - ad4m_create_perspective  → create a named Perspective, returns UUID
  - ad4m_write_link          → write a signed LinkExpression to a Perspective
  - ad4m_read_links          → query links from a Perspective
  - ad4m_list_perspectives   → list all Perspectives on this executor
  - ad4m_get_neighbourhood   → read a shared Neighbourhood by URL

Usage (from any agent):
    from agents.ad4m_tools import AD4M_TOOL_DEFS, execute_ad4m_tool

    def get_tools(self):
        return super().get_tools() + AD4M_TOOL_DEFS

    def execute_tool(self, tool_name, tool_input):
        if tool_name.startswith("ad4m_"):
            return execute_ad4m_tool(tool_name, tool_input)
        return super().execute_tool(tool_name, tool_input)
"""

import json
import os
import urllib.request
import urllib.error
import ssl
import certifi

_SSL_CTX = ssl.create_default_context(cafile=certifi.where())
AD4M_GQL_URL = os.getenv("AD4M_GQL_URL", "http://localhost:4000/graphql")


# ── GraphQL helpers ────────────────────────────────────────────────────────────

def _gql(query: str, variables: dict = None) -> dict:
    """Execute a GraphQL query/mutation against the local AD4M executor."""
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        AD4M_GQL_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as e:
        return {"errors": [{"message": f"AD4M executor not reachable at {AD4M_GQL_URL}: {e}"}]}
    except Exception as e:
        return {"errors": [{"message": str(e)}]}


def _ok(result: dict, key: str) -> str:
    """Extract data[key] from GQL result or return error JSON."""
    if "errors" in result:
        return json.dumps({"error": result["errors"][0]["message"]})
    data = result.get("data", {})
    return json.dumps(data.get(key, data))


# ── Tool implementations ───────────────────────────────────────────────────────

def _agent_status(_: dict) -> str:
    result = _gql("{ agentStatus { isInitialized isUnlocked did } }")
    return _ok(result, "agentStatus")


def _create_perspective(inp: dict) -> str:
    result = _gql(
        """mutation PerspectiveAdd($name: String!) {
             perspectiveAdd(name: $name) { uuid name }
           }""",
        {"name": inp.get("name", "agent-memory")},
    )
    return _ok(result, "perspectiveAdd")


def _write_link(inp: dict) -> str:
    result = _gql(
        """mutation PerspectiveAddLink($uuid: String!, $link: LinkInput!) {
             perspectiveAddLink(uuid: $uuid, link: $link) {
               author timestamp
               data { source predicate target }
             }
           }""",
        {
            "uuid": inp["perspective_uuid"],
            "link": {
                "source":    inp["source"],
                "predicate": inp.get("predicate", "ad4m://relates"),
                "target":    inp["target"],
            },
        },
    )
    return _ok(result, "perspectiveAddLink")


def _read_links(inp: dict) -> str:
    query_obj = {}
    if inp.get("source"):    query_obj["source"]    = inp["source"]
    if inp.get("predicate"): query_obj["predicate"] = inp["predicate"]
    if inp.get("target"):    query_obj["target"]    = inp["target"]

    result = _gql(
        """query PerspectiveQueryLinks($uuid: String!, $query: LinkQuery!) {
             perspectiveQueryLinks(uuid: $uuid, query: $query) {
               author timestamp
               data { source predicate target }
             }
           }""",
        {"uuid": inp["perspective_uuid"], "query": query_obj},
    )
    return _ok(result, "perspectiveQueryLinks")


def _list_perspectives(_: dict) -> str:
    result = _gql("{ perspectives { uuid name sharedUrl state } }")
    return _ok(result, "perspectives")


def _get_neighbourhood(inp: dict) -> str:
    result = _gql(
        """query Perspective($uuid: String!) {
             perspective(uuid: $uuid) {
               uuid name sharedUrl neighbourhood { author timestamp }
             }
           }""",
        {"uuid": inp["uuid"]},
    )
    return _ok(result, "perspective")


# ── Dispatcher ────────────────────────────────────────────────────────────────

_DISPATCH = {
    "ad4m_agent_status":       _agent_status,
    "ad4m_create_perspective": _create_perspective,
    "ad4m_write_link":         _write_link,
    "ad4m_read_links":         _read_links,
    "ad4m_list_perspectives":  _list_perspectives,
    "ad4m_get_neighbourhood":  _get_neighbourhood,
}


def execute_ad4m_tool(tool_name: str, tool_input: dict) -> str:
    fn = _DISPATCH.get(tool_name)
    if fn is None:
        return json.dumps({"error": f"Unknown AD4M tool: {tool_name}"})
    try:
        return fn(tool_input)
    except KeyError as e:
        return json.dumps({"error": f"Missing required field: {e}"})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Tool definitions (Claude tool schema) ─────────────────────────────────────

AD4M_TOOL_DEFS = [
    {
        "name": "ad4m_agent_status",
        "description": "Check the local AD4M agent status — returns DID, initialization state, and whether the keystore is unlocked.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "ad4m_create_perspective",
        "description": "Create a new named Perspective on the local AD4M executor. Returns the UUID needed for writing links.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Human-readable name for this Perspective"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "ad4m_write_link",
        "description": "Write a signed LinkExpression (source → predicate → target) into a Perspective. Use for storing semantic memories, relationships, and facts as graph edges.",
        "input_schema": {
            "type": "object",
            "properties": {
                "perspective_uuid": {"type": "string", "description": "UUID of the target Perspective"},
                "source":           {"type": "string", "description": "Source URI (e.g. 'agent://memory/session-2026-03-22')"},
                "predicate":        {"type": "string", "description": "Predicate URI (e.g. 'ad4m://knows', 'ad4m://decided', 'franc://holds')"},
                "target":           {"type": "string", "description": "Target URI or literal value wrapped as URI (e.g. 'literal://FRANC token graduated')"},
            },
            "required": ["perspective_uuid", "source", "target"],
        },
    },
    {
        "name": "ad4m_read_links",
        "description": "Query links from a Perspective by source, predicate, or target. Any field can be omitted to match all values. Returns author DID + timestamp for each link.",
        "input_schema": {
            "type": "object",
            "properties": {
                "perspective_uuid": {"type": "string", "description": "UUID of the Perspective to query"},
                "source":           {"type": "string", "description": "Filter by source URI (optional)"},
                "predicate":        {"type": "string", "description": "Filter by predicate URI (optional)"},
                "target":           {"type": "string", "description": "Filter by target URI (optional)"},
            },
            "required": ["perspective_uuid"],
        },
    },
    {
        "name": "ad4m_list_perspectives",
        "description": "List all Perspectives on the local AD4M executor, including any joined Neighbourhoods.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "ad4m_get_neighbourhood",
        "description": "Retrieve a Perspective and its Neighbourhood info by UUID. Use to inspect shared semantic graphs from other agents or communities.",
        "input_schema": {
            "type": "object",
            "properties": {
                "uuid": {"type": "string", "description": "Perspective UUID"},
            },
            "required": ["uuid"],
        },
    },
]
