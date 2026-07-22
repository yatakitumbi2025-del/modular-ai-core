#!/usr/bin/env python3
"""
core.py — the orchestrator, now with a verifying code_runner.

    question -> router -> loader -> llm -> ANSWER
                                      -> extract code -> run it -> show real output
                                                      -> if it failed, one fix pass

If no specialist domain matches, it falls back to a general coding prompt.

Run:  python core.py
"""

import sys
import router
import loader
import llm
import tools

GENERAL_SYSTEM = (
    "You are a senior software engineer with deep production experience. "
    "Answer at senior level, concisely and information-dense.\n\n"
    "For any coding answer include: stated assumptions (language/version); clean, "
    "typed, idiomatic code; time AND space complexity in Big-O; the edge cases that "
    "matter, named explicitly (empty, single, duplicates, None, very large, unicode); "
    "real assertions covering them; and a one-line tradeoff vs the obvious alternative.\n\n"
    "CRITICAL — every ```python block must be SELF-CONTAINED and runnable on its own. "
    "It is executed in a fresh process with nothing predefined: no prior variables, no "
    "database connection, no imported context. Include every import, definition and "
    "sample value the block needs. Replace external dependencies with working stand-ins "
    "(sqlite3 in-memory for databases, a literal list for 'users', a temp file for I/O, "
    "a stub class for network clients). End with assertions or a print that proves the "
    "behaviour you claim. Never emit a fragment that references undefined names — it "
    "cannot be verified and is worthless as evidence. If a snippet truly cannot be made "
    "standalone, say so in one line and label it illustrative.\n\n"
    "Never claim code works or state its output from memory; rely on the code_runner "
    "result. If the user's code has a real flaw (injection, race, O(n^2) that should be "
    "O(n), resource leak, unhandled failure), say so directly and early."
)


def run_and_verify(answer_text, system):
    """Find code in the answer, offer to run it, and self-fix once on failure."""
    blocks = tools.extract_python_blocks(answer_text)
    if not blocks:
        return

    for i, code in enumerate(blocks, 1):
        print(f"\n--- code block {i} ---")
        print(code)
        choice = input("Run this code? [Y/n] ").strip().lower()
        if choice == "n":
            print("(skipped)")
            continue

        res = tools.run_python(code)
        print(tools.format_result(res))

        # One automatic self-correction pass if it failed.
        if res["exit_code"] != 0:
            fix = input("It failed. Ask the model to fix it once? [Y/n] ").strip().lower()
            if fix == "n":
                continue
            fix_prompt = (
                "This Python code failed:\n```python\n" + code + "\n```\n"
                "Error output:\n" + res["stderr"] + "\n"
                "Return only the corrected code in a single ```python block."
            )
            print("\n...asking the model to fix it...\n")
            corrected = llm.generate(system, fix_prompt)
            print(corrected)
            fixed_blocks = tools.extract_python_blocks(corrected)
            if fixed_blocks:
                print("\n--- running the fix ---")
                print(tools.format_result(tools.run_python(fixed_blocks[0])))


def calc_and_show(answer_text):
    """Evaluate any arithmetic the model wrote and show the exact results."""
    results = []
    for expr in tools.extract_expressions(answer_text):
        try:
            results.append((expr, tools.safe_calc(expr)))
        except Exception:
            pass  # not real arithmetic — skip it
    if results:
        print("\n" + tools.format_calc(results))


def answer(question, table):
    result = loader.build_context(question, table)

    if result is None:
        print("(no specialist matched — using general coding fallback)")
        system, user = GENERAL_SYSTEM, question
        tool_names = []
        can_run = True   # general fallback is coding-oriented
    else:
        tool_names = result["tools"]
        extra = f", tools: {', '.join(tool_names)}" if tool_names else ""
        print(f"(domain: {result['domain']}{extra})")
        system = result["context"]["system"]
        user = result["context"]["user"]
        can_run = "code_runner" in tool_names

    can_calc = "calculator" in tool_names

    print("...thinking on-device (first answer can be slow)...\n")
    answer_text = llm.generate(system, user)
    print(answer_text)

    if can_run:
        run_and_verify(answer_text, system)
    if can_calc:
        calc_and_show(answer_text)


def main():
    refresh = "--refresh" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--refresh"]

    table = router.build_routing_table(refresh=refresh)
    print(f"Ready. Domains: {', '.join(e['id'] for e in table)}  |  model: {llm.MODEL}\n")

    if args:
        answer(" ".join(args), table)
        return

    print("Ask a coding question (or 'quit'):")
    while True:
        try:
            q = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if q.lower() in ("quit", "exit", ""):
            break
        answer(q, table)


if __name__ == "__main__":
    main()
