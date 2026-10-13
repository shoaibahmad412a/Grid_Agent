"""List the OpenAI model ids your API key can use (key from .env).

Usage: python scripts/list_models.py [filter]     e.g.  python scripts/list_models.py nano
Never prints the key.
"""
import sys

import _common  # noqa: F401

from src.llm import list_models

if __name__ == "__main__":
    word = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    ids = [m for m in list_models() if word in m.lower()]
    print("\n".join(ids) if ids else "no models matched")
    print(f"\n{len(ids)} model(s)")
