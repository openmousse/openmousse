"""假的技能市场（第 5 批第 3 项的测试用）：本机一个 HTTP 服务，几家的回包都是编的，不连网。照 server/evals/fakemodel.py 的写法
（ThreadingHTTPServer、记下每个请求）。同时装成这几家（地址前缀不一样，server.json 的 engine.skills.market 指过来）：
  /clawhub   ClawHub：GET /api/v1/search、GET /api/v1/skills/<slug>、POST /api/v1/skills/-/security-verdicts、GET /api/v1/download
             （zip；GitHub 托管的回 JSON）
  /gh-api    GitHub 的接口：GET /repos/<o>/<r>、/repos/<o>/<r>/commits/<ref>、/repos/<o>/<r>/git/trees/<sha>、/search/code（要令牌）
  /gh-raw    raw.githubusercontent.com：GET /<o>/<r>/<sha>/<路径>
  /skillssh  skills.sh：GET /api/search
  /sites/<名字>  照 agentskills.io 格式发布技能的网站：/.well-known/skills/index.json（v0.1）、/.well-known/agent-skills/index.json（v0.2）
出故障：fail = {"clawhub": 500, "skillssh": "hang"}（那一家回 500 / 一直不回）；verdict 改一个 slug 的信任结论。
"""
from __future__ import annotations

import hashlib
import io
import json
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


def skill_md(name: str, description: str, body: str = "1. Do the thing step by step.") -> bytes:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\n{body}\n".encode()


def make_zip(files: dict[str, bytes], *, top: str = "", symlink: str = "", slip: str = "") -> bytes:
    """一个 zip：files 照放（top 给了就都放进这个顶层文件夹）；symlink = 再放一个叫这个名字的符号链接；slip = 再放一个往外跑的路径。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for rel, data in files.items():
            zf.writestr(f"{top}/{rel}" if top else rel, data)
        if symlink:
            info = zipfile.ZipInfo(f"{top}/{symlink}" if top else symlink)
            info.external_attr = (0o120777 << 16)
            zf.writestr(info, "/etc/passwd")
        if slip:
            zf.writestr(slip, b"owned")
    return buf.getvalue()


def sha_of(*parts: str) -> str:
    return hashlib.sha1("\x00".join(parts).encode()).hexdigest()


class FakeSkillHub:
    def __init__(self) -> None:
        self.clawhub: dict[str, dict] = {}     # slug → {"owner", "version", "files", "summary", "verdict", "github"?, "zip"?}
        self.repos: dict[str, dict] = {}       # "o/r" → {"default", "branches": {名字: sha}, "commits": {sha: {路径: 字节}}, "links": {sha: {路径}}}
        self.skillssh: list[dict] = []         # 搜索回的那几行
        self.sites: dict[str, dict] = {}       # 名字 → {"version": "0.1" | "0.2", "skills": {名字: {"description", "files"} 或 {"data", "type", "digest"?}}}
        self.fail: dict[str, object] = {}
        self.requests: list[dict] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # —— 准备数据 ——
    def add_clawhub(self, slug: str, owner: str, files: dict[str, bytes], *, version: str = "1.0.0", summary: str = "", status: str = "clean",
                    decision: str = "pass", reasons: list[str] | None = None, zip_bytes: bytes | None = None, github: dict | None = None) -> None:
        self.clawhub[slug] = {"owner": owner, "version": version, "files": files, "summary": summary or f"The {slug} skill.", "zip": zip_bytes,
                              "verdict": {"status": status, "decision": decision, "reasons": reasons or []}, "github": github}

    def add_repo(self, full: str, files: dict[str, bytes], *, branch: str = "main", links: set[str] | None = None) -> str:
        """一次新的提交（文件整份给）；同一个分支再加一次 = 分支往前走了一步。→ 提交号。"""
        r = self.repos.setdefault(full, {"default": branch, "branches": {}, "commits": {}, "links": {}})
        sha = sha_of(full, branch, str(len(r["commits"])), json.dumps(sorted(files)))
        r["commits"][sha] = dict(files)
        r["links"][sha] = set(links or ())
        r["branches"][branch] = sha
        return sha

    def add_site(self, name: str, skills: dict[str, dict], version: str = "0.1") -> str:
        self.sites[name] = {"version": version, "skills": skills}
        return f"{self.url}/sites/{name}"

    # —— 地址 ——
    @property
    def url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def market(self, **extra) -> dict:
        """server.json 的 engine.skills.market 指到这里。"""
        return {"clawhub": f"{self.url}/clawhub", "github_api": f"{self.url}/gh-api", "github_raw": f"{self.url}/gh-raw",
                "skills_sh": f"{self.url}/skillssh", "timeout": 2, **extra}

    def start(self) -> FakeSkillHub:
        hub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a) -> None:  # 安静
                pass

            def do_GET(self) -> None:
                hub._handle(self, "GET")

            def do_POST(self) -> None:
                hub._handle(self, "POST")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    def __enter__(self) -> FakeSkillHub:
        return self.start()

    def __exit__(self, *_exc) -> None:
        self.stop()

    # —— 回 ——
    def _send(self, h, status: int, body: bytes | dict | list, ctype: str = "application/json") -> None:
        data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
        h.send_response(status)
        h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)

    def _handle(self, h, method: str) -> None:
        u = urlsplit(h.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        n = int(h.headers.get("Content-Length") or 0)
        body = json.loads(h.rfile.read(n) or b"{}") if n else {}
        self.requests.append({"method": method, "path": u.path, "query": q, "body": body, "auth": bool(h.headers.get("Authorization"))})
        parts = [p for p in u.path.split("/") if p]
        if not parts:
            return self._send(h, 404, {"error": "not found"})
        which = parts[0]
        f = self.fail.get(which)
        if f == "hang":
            time.sleep(6)
            return self._send(h, 504, {"error": "hung"})
        if isinstance(f, int):
            return self._send(h, f, {"error": "broken"})
        try:
            if which == "clawhub":
                return self._clawhub(h, method, parts[1:], q, body)
            if which == "gh-api":
                return self._gh_api(h, parts[1:], q)
            if which == "gh-raw":
                return self._gh_raw(h, parts[1:])
            if which == "skillssh":
                return self._skillssh(h, parts[1:], q)
            if which == "sites":
                return self._site(h, parts[1:])
        except KeyError:
            return self._send(h, 404, {"error": "not found"})
        return self._send(h, 404, {"error": "not found"})

    def _clawhub(self, h, method: str, parts: list[str], q: dict, body: dict) -> None:
        if parts[:2] != ["api", "v1"]:
            raise KeyError
        rest = parts[2:]
        if rest == ["search"]:
            terms = [t for t in q.get("q", "").lower().split() if t]
            rows = [{"score": 1.0, "slug": slug, "displayName": slug, "summary": s["summary"], "version": s["version"], "ownerHandle": s["owner"],
                     "official": False, "source": "clawhub", "install": {"kind": "clawhub", "reference": f"@{s['owner']}/{slug}"}}
                    for slug, s in self.clawhub.items() if not terms or any(t in slug or t in s["summary"].lower() for t in terms)]
            return self._send(h, 200, {"results": rows[: int(q.get("limit", 20))]})
        if rest[:1] == ["skills"] and rest[1:] == ["-", "security-verdicts"] and method == "POST":
            items = []
            for it in body.get("items") or []:
                s = self.clawhub.get(it.get("slug"))
                if s is None:
                    items.append({"ok": False, "requestedSlug": it.get("slug"), "error": {"code": "skill_not_found", "message": "no"}})
                    continue
                v = s["verdict"]
                items.append({"ok": v["decision"] == "pass", "decision": v["decision"], "reasons": v["reasons"], "slug": it["slug"],
                              "version": s["version"], "overview": "A fictional overview.", "securityAuditUrl": "https://example.invalid/audit",
                              "security": {"status": v["status"], "passed": v["decision"] == "pass", "summary": f"{v['status']} (fake)"}})
            return self._send(h, 200, {"schema": "clawhub.skill.security-verdicts.v1", "items": items})
        if rest[:1] == ["skills"] and len(rest) == 2:
            s = self.clawhub[rest[1]]
            if q.get("ownerHandle") and q["ownerHandle"] != s["owner"]:
                raise KeyError
            return self._send(h, 200, {"skill": {"slug": rest[1], "displayName": rest[1], "summary": s["summary"], "tags": {"latest": s["version"]}},
                                       "latestVersion": {"version": s["version"]}, "owner": {"handle": s["owner"]},
                                       "moderation": {"isMalwareBlocked": False, "verdict": s["verdict"]["status"]}})
        if rest == ["download"]:
            s = self.clawhub[q["slug"]]
            if q.get("version") != s["version"]:
                raise KeyError
            if s["github"]:
                return self._send(h, 200, {"sourceRef": "public-github", **s["github"]})
            data = s["zip"] if s["zip"] is not None else make_zip(s["files"])
            return self._send(h, 200, data, "application/zip")
        raise KeyError

    def _gh_api(self, h, parts: list[str], q: dict) -> None:
        if parts[:1] == ["search"] and parts[1:] == ["code"]:
            if not h.headers.get("Authorization"):
                return self._send(h, 401, {"message": "Requires authentication"})
            items = []
            terms = [t for t in q.get("q", "").lower().replace("filename:skill.md", "").split() if t]
            for full, r in self.repos.items():
                files = r["commits"][r["branches"][r["default"]]]
                for path in files:
                    if path.endswith("SKILL.md") and any(t in path.lower() for t in terms):
                        items.append({"name": "SKILL.md", "path": path, "repository": {"full_name": full}})
            return self._send(h, 200, {"items": items})
        if parts[:1] != ["repos"] or len(parts) < 3:
            raise KeyError
        full = f"{parts[1]}/{parts[2]}"
        r = self.repos[full]
        rest = parts[3:]
        if not rest:
            return self._send(h, 200, {"full_name": full, "default_branch": r["default"]})
        if rest[0] == "commits" and len(rest) == 2:
            ref = rest[1]
            sha = r["branches"].get(ref) or (ref if ref in r["commits"] else None)
            if sha is None:
                raise KeyError
            return self._send(h, 200, {"sha": sha})
        if rest[:2] == ["git", "trees"] and len(rest) == 3:
            sha = rest[2]
            files = r["commits"][sha]
            tree = [{"path": p, "type": "blob", "mode": "120000" if p in r["links"][sha] else "100644", "size": len(b)} for p, b in files.items()]
            dirs = sorted({p.rsplit("/", 1)[0] for p in files if "/" in p})
            tree += [{"path": d, "type": "tree", "mode": "040000"} for d in dirs]
            return self._send(h, 200, {"sha": sha, "tree": tree, "truncated": False})
        raise KeyError

    def _gh_raw(self, h, parts: list[str]) -> None:
        full, sha, path = f"{parts[0]}/{parts[1]}", parts[2], "/".join(parts[3:])
        return self._send(h, 200, self.repos[full]["commits"][sha][path], "text/plain")

    def _skillssh(self, h, parts: list[str], q: dict) -> None:
        if parts != ["api", "search"]:
            raise KeyError
        terms = [t for t in q.get("q", "").lower().split() if t]
        rows = [r for r in self.skillssh if not terms or any(t in r["skillId"].lower() for t in terms)]
        return self._send(h, 200, {"query": q.get("q", ""), "skills": rows, "count": len(rows)})

    def _site(self, h, parts: list[str]) -> None:
        site = self.sites[parts[0]]
        rest = parts[1:]
        if rest == [".well-known", "skills", "index.json"] and site["version"] == "0.1":
            return self._send(h, 200, {"skills": [{"name": n, "description": s.get("description", ""), "files": sorted(s["files"])}
                                                  for n, s in site["skills"].items()]})
        if rest == [".well-known", "agent-skills", "index.json"] and site["version"] == "0.2":
            out = []
            for n, s in site["skills"].items():
                out.append({"name": n, "type": s.get("type", "skill-md"), "description": s.get("description", ""), "url": f"/sites/{parts[0]}/blobs/{n}",
                            "digest": s.get("digest") or "sha256:" + hashlib.sha256(s["data"]).hexdigest()})
            return self._send(h, 200, {"$schema": "https://schemas.agentskills.io/discovery/0.2.0/schema.json", "skills": out})
        if rest[:2] == [".well-known", "skills"] and len(rest) >= 4 and site["version"] == "0.1":
            return self._send(h, 200, site["skills"][rest[2]]["files"]["/".join(rest[3:])], "text/plain")
        if rest[:1] == ["blobs"] and len(rest) == 2:
            return self._send(h, 200, site["skills"][rest[1]]["data"], "application/octet-stream")
        raise KeyError
