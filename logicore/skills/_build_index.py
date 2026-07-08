#!/usr/bin/env python3
"""
Build SKILL_INDEX.md from individual SKILL.md files.

Run this script whenever skills are added, removed, or modified.
Prevents index drift by regenerating from source of truth.

Usage:
    python logicore/skills/_build_index.py [skills_dir]

If no skills_dir is provided, defaults to logicore/skills/defaults/
"""

import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from logicore.skills.loader import SkillLoader


def main():
    if len(sys.argv) > 1:
        skills_dir = sys.argv[1]
    else:
        # Default to logicore/skills/defaults/
        skills_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "defaults")
    
    if not os.path.exists(skills_dir):
        print(f"Error: Skills directory not found: {skills_dir}")
        sys.exit(1)
    
    print(f"Building SKILL_INDEX.md from: {skills_dir}")
    content = SkillLoader.build_skill_index(skills_dir)
    print(f"Generated SKILL_INDEX.md:")
    print(content)


if __name__ == "__main__":
    main()
