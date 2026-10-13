"""Send ONE tiny request to the OpenAI API and print what came back.

Use it after filling in .env, before any benchmark run. It costs a fraction
of a cent. It prints the model, latency, token counts and the reply, and never
prints the API key.
Usage: python scripts/check_api.py
"""
import _common  # noqa: F401

from src.edits import parse_edits
from src.llm import APILLM

if __name__ == "__main__":
    api = APILLM()
    print(f"model: {api.model}   effort: {api.effort or 'model default'}")
    text, meta = api.complete([
        {"role": "system", "content": 'Reply with exactly this JSON and nothing else: {"edits": [], "refuse": true, "reason": "test"}'},
        {"role": "user", "content": "connection test"},
    ])
    print(f"model reported: {meta['model']}   latency: {meta['latency_s']:.1f} s   "
          f"tokens in/out: {meta['tokens_in']}/{meta['tokens_out']}")
    if meta["error"]:
        raise SystemExit(f"ERROR: {meta['error']}")
    print(f"reply: {text!r}")
    p = parse_edits(text)
    print("parses as edit JSON: " + ("yes" if not hasattr(p, "kind") else f"no ({p.message})"))
