import json, os, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import router, loader, llm, tools, core

HOST = "127.0.0.1"
PORT = int(os.environ.get("MODULAR_AI_PORT", 8000))
UI_FILE = Path(__file__).parent / "ui.html"
_table = None
_lock = threading.Lock()

def get_table(refresh=False):
    global _table
    with _lock:
        if _table is None or refresh:
            _table = router.build_routing_table(refresh=refresh)
    return _table

def status():
    try: domains = [e["id"] for e in get_table()]
    except Exception: domains = []
    return {"domains": domains, "model": llm.MODEL,
            "groq": bool(os.environ.get("GROQ_API_KEY")),
            "jina": bool(os.environ.get("JINA_API_KEY"))}

def ask(question):
    r = loader.build_context(question, get_table())
    if r is None:
        system, user = core.GENERAL_SYSTEM, question
        domains, tool_names = ["general"], []
        _retrieval = "unrouted"
    else:
        system = r["context"]["system"]; user = r["context"]["user"]
        _retrieval = "unset"
        try:
            import retrieve as _rt
            _PER_PACK = 4
            _TOTAL = 6
            _pids = r.get("domains") or ([r["domain"]] if r.get("domain") else [])
            _pool = []
            for _p in _pids:
                try:
                    for _s, _t in _rt.retrieve(question, _p, k=_PER_PACK):
                        _pool.append((_s, _t))
                except Exception as _pe:
                    print("retrieval failed for", _p, ":", _pe)
            _seen = set()
            _hits = []
            for _s, _t in sorted(_pool, key=lambda x: -x[0]):
                if _t in _seen:
                    continue
                _seen.add(_t)
                _hits.append((_s, _t))
                if len(_hits) >= _TOTAL:
                    break
            if _hits:
                _ref = "\n\n".join(t for _, t in _hits)
                system = (
                    "Background knowledge you have. Treat it as your own knowledge and "
                    "answer directly from it. If it does not cover the question, say so "
                    "plainly rather than inventing API names, flags, or version numbers. "
                    "Never mention packs, modules, sources, or where this information "
                    "came from. Do not append notes labelled by pack name.\n\n"
                    + _ref + "\n\n---\n\n" + system
                )
                _retrieval = "ok:%d/%d" % (len(_hits), len(_pids))
            else:
                _retrieval = "empty" if _pids else "nopack"
        except Exception as _e:
            print("retrieval skipped:", _e)
            _retrieval = "error"
        if r.get("degraded"):
            _retrieval += "|stale:" + ",".join(r["degraded"])
        domains = r.get("domains", [r["domain"]]); tool_names = r["tools"]
    answer = llm.generate(system, user)
    blocks = tools.extract_python_blocks(answer) if "code_runner" in tool_names else []
    return {"answer": answer, "domains": domains, "tools": tool_names, "blocks": blocks, "retrieval": _retrieval}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data))); self.end_headers()
        self.wfile.write(data)
    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            if not UI_FILE.exists(): return self._send(500, {"error": "ui.html missing"})
            return self._send(200, UI_FILE.read_bytes(), "text/html; charset=utf-8")
        if self.path == "/api/status": return self._send(200, status())
        self._send(404, {"error": "not found"})
    def do_POST(self):
        try:
            b = self._body()
            if self.path == "/api/ask":
                q = (b.get("question") or "").strip()
                if not q: return self._send(400, {"error": "empty question"})
                return self._send(200, ask(q))
            if self.path == "/api/run": return self._send(200, run_code(b.get("code","")))
            if self.path == "/api/keys":
                if b.get("groq"): os.environ["GROQ_API_KEY"] = b["groq"].strip()
                if b.get("jina"): os.environ["JINA_API_KEY"] = b["jina"].strip()
                return self._send(200, status())
            if self.path == "/api/refresh":
                get_table(refresh=True); return self._send(200, status())
            self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

if __name__ == "__main__":
    print(f"Modular AI web UI  ->  http://{HOST}:{PORT}\nCtrl+C to stop.\n")
    HTTPServer((HOST, PORT), H).serve_forever()


def run_code(src, system=None):
    result = tools.run_python(src)
    result["verdict"] = "OK" if result["exit_code"] == 0 else "FAILED"
    result["fixed"] = None
    if result["exit_code"] != 0:
        fix_prompt = (
            "This code failed when executed:\n\n```python\n" + src + "\n```\n\n"
            "Error:\n" + result["stderr"] + "\n\n"
            "State the root cause in one line, then give the corrected, complete, "
            "self-contained code in a single ```python block."
        )
        fix_answer = llm.generate(
            system or "You are a careful Python debugger. Be concise.", fix_prompt
        )
        fixed_blocks = tools.extract_python_blocks(fix_answer)
        if fixed_blocks:
            fixed_src = fixed_blocks[0]
            fixed_res = tools.run_python(fixed_src)
            fixed_res["verdict"] = "OK" if fixed_res["exit_code"] == 0 else "FAILED"
            result["fix_explanation"] = fix_answer
            result["fixed_code"] = fixed_src
            result["fixed"] = fixed_res
    return result
