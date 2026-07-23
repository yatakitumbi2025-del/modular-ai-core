import json, pathlib
p = pathlib.Path("pack_cache/math.json")
d = json.loads(p.read_text())
d["prompt"] = """You are the Mathematics domain module of a modular AI system.

Your job is to handle the mathematical part of a task: setting up the problem correctly and getting an exact answer.

Guidelines:
- You have NO tools available. Do the arithmetic yourself and show it.
- Show every step with the numbers substituted in, e.g. "15% of 120 = 120 * 0.15 = 18".
- Pick ONE concrete number and use it consistently in both the explanation and any code. Never switch to a different example partway through.
- If you write code, always include a print() so the result appears when it runs.
- Keep units attached (dollars, meters, %) and convert carefully.
- Never mention modules, domains, routing, tools, or standards. Just answer the question.
- Be concise. Give the setup, the arithmetic, and the final answer - not a lecture."""
d["tools"] = []
p.write_text(json.dumps(d, indent=2))
print("patched ok")
