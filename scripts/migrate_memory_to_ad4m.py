#!/usr/bin/env python3
"""
Migrate Claude memory files to AD4M ClaudeMemory Perspective.
Reads all .md files from ~/.claude/projects/-Users-multiuniverse/memory/
and writes each as LinkExpressions into the ClaudeMemory perspective.
"""

import json
import os
import re
import urllib.request

MEMORY_DIR = os.path.expanduser("~/.claude/projects/-Users-multiuniverse/memory")
UUID_FILE  = os.path.expanduser("~/.ad4m/claude-memory-uuid")
GQL_URL    = "http://localhost:4000/graphql"

def gql(query, variables=None):
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(GQL_URL, data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    if data.get("errors"):
        raise RuntimeError(data["errors"][0]["message"])
    return data["data"]

def write_link(uuid, source, predicate, target):
    return gql(
        """mutation PerspectiveAddLink($uuid: String!, $link: LinkInput!) {
             perspectiveAddLink(uuid: $uuid, link: $link) {
               data { source predicate target }
             }
           }""",
        {"uuid": uuid, "link": {"source": source, "predicate": predicate, "target": target}},
    )

def parse_frontmatter(content):
    match = re.match(r"^---\n(.+?)\n---", content, re.DOTALL)
    if not match:
        return {}, content
    fm = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    body = content[match.end():].strip()
    return fm, body

def slug(filename):
    return filename.replace(".md", "").replace(" ", "-").lower()

def main():
    with open(UUID_FILE) as f:
        perspective_uuid = f.read().strip()

    files = [
        fn for fn in os.listdir(MEMORY_DIR)
        if fn.endswith(".md") and fn != "MEMORY.md"
    ]
    files.sort()

    print(f"Perspective: {perspective_uuid}")
    print(f"Migrating {len(files)} memory files...\n")

    ok = 0
    for fn in files:
        path = os.path.join(MEMORY_DIR, fn)
        with open(path, encoding="utf-8") as f:
            content = f.read()

        fm, body = parse_frontmatter(content)
        mem_type = fm.get("type", "reference")
        mem_name = fm.get("name", fn.replace(".md", ""))
        source   = f"memory://{mem_type}/{slug(fn)}"

        try:
            write_link(perspective_uuid, source, "ad4m://has-name",    f"literal://{mem_name}")
            write_link(perspective_uuid, source, "ad4m://has-content",  f"literal://{content}")
            print(f"  ✓ {fn} → {source}")
            ok += 1
        except Exception as e:
            print(f"  ✗ {fn}: {e}")

    print(f"\nDone: {ok}/{len(files)} memories written to AD4M ClaudeMemory")

if __name__ == "__main__":
    main()
