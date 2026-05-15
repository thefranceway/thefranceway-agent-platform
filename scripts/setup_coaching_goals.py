#!/usr/bin/env python3
"""
Coaching Goals Setup — define your life goals and work OKRs.

Stores goals in the kb_coaching vector store so all coaching agents
(Morning Briefing, Life Coach, Work Coach) can access them.

Usage:
    python scripts/setup_coaching_goals.py              # interactive
    python scripts/setup_coaching_goals.py --list       # show current goals
    python scripts/setup_coaching_goals.py --clear      # archive all goals
    python scripts/setup_coaching_goals.py --add-goal "Reach peak physical and mental performance" --area health
    python scripts/setup_coaching_goals.py --add-okr "Launch FRANC token gate" --kr "1000 FRANC holders"
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.base_agent import JSONVectorStore

PLATFORM_DIR = Path(__file__).parent.parent
CONFIG_PATH  = PLATFORM_DIR / "registry" / "coaching_config.json"


def load_store():
    return JSONVectorStore("kb_coaching")


def list_goals():
    store   = load_store()
    results = store.search("goal OKR life work", top_k=50)
    goals   = [r for r in results if r.get("metadata", {}).get("type") in ("goal", "okr")
               and r.get("metadata", {}).get("status") != "archived"]

    if not goals:
        print("No goals set yet.")
        return

    life_goals = [g for g in goals if g.get("metadata", {}).get("type") == "goal"]
    okrs       = [g for g in goals if g.get("metadata", {}).get("type") == "okr"]

    print("\n🎯 LIFE GOALS")
    print("─" * 40)
    for g in life_goals:
        meta = g.get("metadata", {})
        area = meta.get("area", "general")
        print(f"  [{area.upper()}] {g['text']}")
        print(f"          Added: {g.get('added_at', '')[:10]}  ID: {g['id'][:8]}")

    print("\n📊 WORK OKRs")
    print("─" * 40)
    for g in okrs:
        meta = g.get("metadata", {})
        krs  = meta.get("key_results", [])
        print(f"  O: {g['text']}")
        for kr in krs:
            print(f"     KR: {kr}")
        print(f"     Added: {g.get('added_at', '')[:10]}  ID: {g['id'][:8]}")

    print()


def add_life_goal(text: str, area: str = "life"):
    store = load_store()
    store.add(text, metadata={
        "type":   "goal",
        "area":   area,
        "status": "active",
        "source": "setup_coaching_goals",
    })
    print(f"✅ Life goal added [{area}]: {text}")


def add_okr(objective: str, key_results: list[str]):
    store = load_store()
    text  = f"OKR Objective: {objective}"
    store.add(text, metadata={
        "type":        "okr",
        "objective":   objective,
        "key_results": key_results,
        "status":      "active",
        "source":      "setup_coaching_goals",
    })
    print(f"✅ OKR added: {objective}")
    for kr in key_results:
        print(f"   KR: {kr}")


def archive_all():
    store   = load_store()
    results = store.search("goal OKR", top_k=50)
    count   = 0
    for r in results:
        if r.get("metadata", {}).get("status") == "active":
            r["metadata"]["status"] = "archived"
            count += 1
    store._save()
    print(f"Archived {count} goals.")


def interactive_setup():
    print("\n╔══════════════════════════════════════════╗")
    print("║   Coaching Goals Setup — thefranceway    ║")
    print("╚══════════════════════════════════════════╝\n")

    # Location config
    config = {}
    if CONFIG_PATH.exists():
        config = json.loads(CONFIG_PATH.read_text())

    print("📍 Location (for weather briefing)")
    city = input(f"  City [{config.get('city', 'Mexico City')}]: ").strip()
    if city:
        # Basic lat/lon lookup hints
        city_coords = {
            "mexico city": (19.4326, -99.1332, "America/Mexico_City"),
            "shanghai":    (31.2304, 121.4737, "Asia/Shanghai"),
            "new york":    (40.7128, -74.0060,  "America/New_York"),
            "london":      (51.5074, -0.1278,   "Europe/London"),
            "miami":       (25.7617, -80.1918,  "America/New_York"),
            "los angeles": (34.0522, -118.2437, "America/Los_Angeles"),
        }
        city_lower = city.lower()
        for key, (lat, lon, tz) in city_coords.items():
            if key in city_lower:
                config.update({"city": city, "latitude": lat, "longitude": lon, "timezone": tz})
                print(f"  ✅ Coordinates set for {city}")
                break
        else:
            lat = float(input("  Latitude (e.g. 19.4326): ").strip() or config.get("latitude", 19.4326))
            lon = float(input("  Longitude (e.g. -99.1332): ").strip() or config.get("longitude", -99.1332))
            tz  = input(f"  Timezone (e.g. America/Mexico_City) [{config.get('timezone', 'America/Mexico_City')}]: ").strip()
            config.update({
                "city": city,
                "latitude": lat,
                "longitude": lon,
                "timezone": tz or config.get("timezone", "America/Mexico_City"),
            })
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2))
    print(f"  Config saved to {CONFIG_PATH}\n")

    # Life goals
    print("🎯 LIFE GOALS  (3-5 recommended)")
    print("  Areas: life, health, relationships, finance, creativity, growth")
    print("  Enter goals one at a time. Press Enter on blank line when done.\n")

    life_goal_examples = [
        "Reach peak physical and mental performance — consistent energy 8+/10",
        "Deepen 3 high-quality relationships in longevity / DeSci space",
        "Create financial runway of 12 months through token + partnerships",
        "Ship 2 pieces of original research or thought leadership per month",
    ]
    print("  Examples:")
    for ex in life_goal_examples:
        print(f"    • {ex}")
    print()

    store = load_store()
    while True:
        goal = input("  Life goal: ").strip()
        if not goal:
            break
        area = input(f"  Area [life/health/relationships/finance/creativity/growth]: ").strip() or "life"
        store.add(goal, metadata={
            "type": "goal", "area": area, "status": "active", "source": "setup_coaching_goals"
        })
        print(f"  ✅ Saved\n")

    # Work OKRs
    print("\n📊 WORK OKRs")
    print("  Enter 1-3 Objectives. For each, add 2-3 Key Results.")
    print("  Keep OKRs to this quarter or next 30-90 days.\n")

    okr_examples = [
        "O: Launch FRANC token ecosystem and reach 500 active holders",
        "O: Establish thefranceway as the go-to DeSci partnerships voice in LATAM",
    ]
    print("  Examples:")
    for ex in okr_examples:
        print(f"    {ex}")
    print()

    while True:
        obj = input("  Objective (or Enter to skip): ").strip()
        if not obj:
            break
        krs = []
        print(f"  Key Results for: {obj}")
        while True:
            kr = input(f"    KR {len(krs)+1} (or Enter when done): ").strip()
            if not kr:
                break
            krs.append(kr)
        store.add(f"OKR Objective: {obj}", metadata={
            "type":        "okr",
            "objective":   obj,
            "key_results": krs,
            "status":      "active",
            "source":      "setup_coaching_goals",
        })
        print(f"  ✅ OKR saved\n")

    print("\n✅ Setup complete. Coaching agents are ready.")
    print("   Morning Briefing: 6am daily")
    print("   Life Coach:       8pm daily")
    print("   Work Coach:       9am Sundays")
    print("\n   Run 'python scripts/setup_coaching_goals.py --list' to review.\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Coaching Goals Setup")
    parser.add_argument("--list",      action="store_true",   help="List all active goals")
    parser.add_argument("--clear",     action="store_true",   help="Archive all goals")
    parser.add_argument("--add-goal",  type=str,              help="Add a life goal")
    parser.add_argument("--area",      type=str, default="life", help="Goal area")
    parser.add_argument("--add-okr",   type=str,              help="Add an OKR objective")
    parser.add_argument("--kr",        action="append", default=[], help="Key result (use multiple --kr)")
    args = parser.parse_args()

    if args.list:
        list_goals()
    elif args.clear:
        archive_all()
    elif args.add_goal:
        add_life_goal(args.add_goal, args.area)
    elif args.add_okr:
        add_okr(args.add_okr, args.kr)
    else:
        interactive_setup()


if __name__ == "__main__":
    main()
