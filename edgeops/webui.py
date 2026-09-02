"""FastAPI 交互后端：会话管理 + 后台任务 + 增量轮询 + 配置/知识库 API。"""

import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path

import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from . import agent as agent_mod
from . import knowledge, prompts
from .config import DEFAULTS, load_config, config_search_paths
from .gateway import Gateway, GatewayError
from .llm import LLMClient, LLMError
from .tools import OtaChecker

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
SESSIONS_DIR = ROOT / "sessions"


def _find_default_config():
    for p in config_search_paths():
        if Path(p).is_file():
            return Path(p)
    return ROOT / "config.json"


CONFIG_PATH = (Path(os.environ["EDGEOPS_CONFIG"]).absolute()
               if os.environ.get("EDGEOPS_CONFIG") else None) or _find_default_config()

app = FastAPI(title="EdgeOps Agent")

SESSIONS = {}
TASKS = {}
LOCK = threading.RLock()


def _merge(base, override):
    out = json.loads(json.dumps(base))
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        elif v is not None:
            out[k] = v
    return out


def _load_raw():
    data = {}
    if CONFIG_PATH.is_file():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    return _merge(DEFAULTS, data)


def _save_raw(data):
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)


def _err(status, msg):
    return JSONResponse(status_code=status, content={"ok": False, "error": msg})


@app.exception_handler(Exception)
async def on_unexpected(request, exc):
    return JSONResponse(status_code=500,
                        content={"ok": False, "error": f"{type(exc).__name__}: {exc}"})


# ---------- 会话持久化 ----------

_SESSION_FIELDS = ("session_id", "title", "created_at", "updated_at", "messages")


def _save_session(s):
    try:
        SESSIONS_DIR.mkdir(exist_ok=True)
        p = SESSIONS_DIR / f"{s['session_id']}.session.json"
        snap = {k: s.get(k) for k in _SESSION_FIELDS}
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except (OSError, TypeError):
        pass


def _load_sessions_from_disk():
    try:
        SESSIONS_DIR.mkdir(exist_ok=True)
    except OSError:
        return
    for f in SESSIONS_DIR.glob("*.session.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        sid = d.get("session_id") or f.name.replace(".session.json", "")
        d["session_id"] = sid
        d.setdefault("messages", [])
        # 恢复时清理运行态：服务重启后不可能还有任务在跑，
        # 残留的 running 状态会让前端永远转圈
        for m in d["messages"]:
            if m.get("status") == "running":
                m["status"] = "interrupted"
                m["error"] = "服务重启，本轮回答已中断（重新提问即可）"
            m.pop("running", None)
        SESSIONS[sid] = d


_load_sessions_from_disk()


# ---------- 页面 ----------

def _ui_version():
    try:
        return hashlib.md5((WEB_DIR / "index.html").read_bytes()).hexdigest()[:10]
    except OSError:
        return "unknown"


@app.get("/")
def index():
    data = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    data = data.replace("</head>",
                        f'<script>window.UI_BUILD="{_ui_version()}"</script></head>')
    resp = Response(content=data, media_type="text/html")
    resp.headers["Cache-Control"] = "no-store, must-revalidate"
    return resp


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


@app.get("/api/uiversion")
def uiversion():
    return {"ok": True, "version": _ui_version()}


# ---------- 会话 API ----------

@app.get("/api/sessions")
def list_sessions():
    with LOCK:
        items = []
        for sid, s in sorted(SESSIONS.items(),
                             key=lambda kv: kv[1].get("updated_at") or 0, reverse=True)[:30]:
            items.append({"session_id": sid, "title": s.get("title") or "新会话",
                          "updated_at": s.get("updated_at"),
                          "count": len(s.get("messages", []))})
    return {"ok": True, "items": items}


@app.post("/api/sessions")
def create_session():
    sid = uuid.uuid4().hex[:12]
    with LOCK:
        SESSIONS[sid] = {"session_id": sid, "title": "", "created_at": time.time(),
                         "updated_at": time.time(), "messages": []}
        _save_session(SESSIONS[sid])
    return {"ok": True, "session_id": sid}


@app.get("/api/sessions/{sid}")
def get_session(sid: str):
    with LOCK:
        s = SESSIONS.get(sid)
        if not s:
            return _err(404, "会话不存在")
        return {"ok": True, "session_id": sid, "title": s.get("title") or "新会话",
                "messages": s.get("messages", [])}


@app.delete("/api/sessions/{sid}")
def delete_session(sid: str):
    with LOCK:
        if sid not in SESSIONS:
            return _err(404, "会话不存在")
        SESSIONS.pop(sid)
    try:
        (SESSIONS_DIR / f"{sid}.session.json").unlink(missing_ok=True)
    except OSError:
        pass
    return {"ok": True}


# ---------- 消息/任务 API ----------

def _emit(sid, tid, msg):
    with LOCK:
        t = TASKS.get(tid)
        if t:
            ts = time.strftime("%H:%M:%S")
            t["log"].append(f"[{ts}] {msg}")
            t["stage"] = msg


@app.post("/api/sessions/{sid}/message")
async def post_message(sid: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        return _err(400, "请求体不是合法 JSON")
    text = str(body.get("text") or "").strip()
    if not text:
        return _err(400, "消息为空")
    with LOCK:
        s = SESSIONS.get(sid)
        if not s:
            return _err(404, "会话不存在")
        if any(m.get("running") for m in s["messages"]):
            return _err(400, "上一条消息仍在处理中，请稍候或点停止")

    tid = uuid.uuid4().hex[:12]
    ts = time.time()
    user_msg = {"role": "user", "content": text, "ts": ts}
    ai_msg = {"role": "assistant", "content": "", "ts": ts,
              "task_id": tid, "status": "running", "running": True,
              "tool_log": [], "plan": None}
    with LOCK:
        s["messages"].append(user_msg)
        s["messages"].append(ai_msg)
        if not s.get("title"):
            s["title"] = text[:24]
        s["updated_at"] = ts
        try:
            cfg = load_config()
        except Exception as e:
            ai_msg.update({"status": "failed", "error": f"配置错误: {e}"})
            ai_msg.pop("running", None)
            _save_session(s)
            return _err(400, f"配置错误: {e}")
        _save_session(s)
        TASKS[tid] = {"task_id": tid, "session_id": sid, "status": "running",
                      "stage": "排队中", "log": [], "llm_live": {},
                      "created_at": ts, "cancel": False}
    threading.Thread(target=_run_agent_task, args=(sid, tid, text), daemon=True).start()
    return {"ok": True, "task_id": tid}


def _run_agent_task(sid, tid, text):
    with LOCK:
        s = SESSIONS.get(sid)
        if not s:
            return
        ai_msg = s["messages"][-1]
        live = {}
        TASKS[tid]["llm_live"] = live

    def cancelled():
        with LOCK:
            return bool(TASKS.get(tid, {}).get("cancel"))

    try:
        cfg = load_config()
        history = agent_mod.build_history(
            [m for m in s["messages"][:-2] if m.get("role") in ("user", "assistant")],
            rounds=int(cfg.get("agent", {}).get("history_rounds", 6)))
        res = agent_mod.run(text, cfg, history=history,
                            progress=lambda m: _emit(sid, tid, m),
                            llm_live=live, cancel_check=cancelled)
        with LOCK:
            ai_msg.update({"content": res["answer"], "status": "success",
                           "tool_log": res["tool_log"], "plan": res["plan"]})
            ai_msg.pop("running", None)
            s["updated_at"] = time.time()
            _save_session(s)
            t = TASKS.get(tid)
            if t:
                t.update({"status": "success", "stage": "完成",
                          "finished_at": time.time()})
                _emit(sid, tid, "✓ 回答完成")
    except agent_mod.TaskCancelled:
        with LOCK:
            ai_msg.update({"status": "failed", "error": "已取消"})
            ai_msg.pop("running", None)
            s["updated_at"] = time.time()
            _save_session(s)
            _emit(sid, tid, "⏹ 已取消")
            t = TASKS.get(tid)
            if t:
                t.update({"status": "failed", "stage": "已取消",
                          "finished_at": time.time()})
    except Exception as e:
        with LOCK:
            ai_msg.update({"status": "failed",
                           "error": f"{type(e).__name__}: {e}"})
            ai_msg.pop("running", None)
            s["updated_at"] = time.time()
            _save_session(s)
            _emit(sid, tid, f"✗ 失败: {type(e).__name__}: {e}")
            t = TASKS.get(tid)
            if t:
                t.update({"status": "failed", "stage": "失败",
                          "finished_at": time.time()})


@app.get("/api/task/{tid}")
def get_task(tid: str):
    with LOCK:
        t = TASKS.get(tid)
        if not t:
            return _err(404, "任务不存在")
        return {
            "ok": True,
            "task_id": tid,
            "status": t["status"],
            "stage": t["stage"],
            "log": t.get("log", []),
            "llm_live": {k: "".join(v)[-900:] for k, v in (t.get("llm_live") or {}).items()},
        }


@app.post("/api/task/{tid}/cancel")
def cancel_task(tid: str):
    with LOCK:
        t = TASKS.get(tid)
        if not t:
            return _err(404, "任务不存在")
        if t["status"] != "running":
            return _err(400, "任务未在运行")
        t["cancel"] = True
        t["stage"] = "正在停止…"
        ts = time.strftime("%H:%M:%S")
        t["log"].append(f"[{ts}] ⏹ 收到停止请求，等待任务退出…")
    return {"ok": True}


# ---------- 知识库 API ----------

@app.get("/api/kb")
def kb_index():
    try:
        return {"ok": True, "items": knowledge.topics()}
    except knowledge.KnowledgeError as e:
        return _err(500, str(e))


@app.get("/api/kb/{topic_id}")
def kb_doc(topic_id: str):
    doc = knowledge.get_doc(topic_id)
    if doc is None:
        return _err(404, "主题不存在")
    t = next((x for x in knowledge.topics() if x["id"] == topic_id), {})
    return {"ok": True, "id": topic_id, "title": t.get("title", topic_id), "content": doc}


# ---------- 配置 API ----------

def _mask(v):
    v = str(v or "")
    return "*" * len(v) if v else ""


def _is_masked(v):
    return bool(v) and set(v) <= {"*"} and len(v) >= 4


@app.get("/api/config")
def get_config():
    c = _load_raw()
    return {
        "ok": True,
        "config_path": str(CONFIG_PATH),
        "llm": {
            "base_url": c["llm"].get("base_url", ""),
            "model": c["llm"].get("model", ""),
            "temperature": c["llm"].get("temperature", 0.2),
            "api_key": _mask(c["llm"].get("api_key")),
            "api_key_set": bool(c["llm"].get("api_key")),
        },
        "gateway": {
            "url": c["gateway"].get("url", ""),
            "token": _mask(c["gateway"].get("token")),
            "token_set": bool(c["gateway"].get("token")),
        },
        "ota": {
            "url": c["ota"].get("url", ""),
            "token": _mask(c["ota"].get("token")),
        },
    }


@app.post("/api/config")
async def save_config(request: Request):
    body = await request.json()
    raw = _load_raw()

    llm = body.get("llm") or {}
    for key in ("base_url", "model"):
        if key in llm:
            raw["llm"][key] = str(llm[key]).strip()
    if "temperature" in llm and llm["temperature"] not in ("", None):
        try:
            raw["llm"]["temperature"] = float(llm["temperature"])
        except (TypeError, ValueError):
            pass
    if llm.get("api_key") and not _is_masked(llm["api_key"]):
        raw["llm"]["api_key"] = str(llm["api_key"]).strip()

    gw = body.get("gateway") or {}
    if "url" in gw:
        raw["gateway"]["url"] = str(gw["url"]).strip().rstrip("/")
    if gw.get("token") and not _is_masked(gw["token"]):
        raw["gateway"]["token"] = str(gw["token"]).strip()

    ota = body.get("ota") or {}
    if "url" in ota:
        raw["ota"]["url"] = str(ota["url"]).strip().rstrip("/")
    if ota.get("token") and not _is_masked(ota["token"]):
        raw["ota"]["token"] = str(ota["token"]).strip()

    _save_raw(raw)
    return get_config()


@app.post("/api/test")
async def test_conn():
    problems = []
    try:
        cfg = load_config()
    except Exception as e:
        return {"ok": False, "error": str(e)}

    llm_result = {"ok": False, "message": ""}
    try:
        client = LLMClient(cfg["llm"])
        reply = client.chat("你是连通性测试端点。只回答两个字母: OK", "ping", retries=0)
        llm_result = {"ok": True,
                      "message": f"{cfg['llm']['model']} 回复: {(reply or '').strip()[:40]}"}
    except LLMError as e:
        llm_result = {"ok": False, "message": str(e)[:200]}
        problems.append("LLM")

    gw_result = {"ok": False, "message": "未配置网关地址"}
    if cfg["gateway"].get("url"):
        try:
            gw = Gateway(cfg["gateway"])
            gw.auth()
            ok, info = gw.query("system/info")
            ver = (info or {}).get("version") if ok and isinstance(info, dict) else ""
            gw_result = {"ok": True,
                         "message": f"{cfg['gateway']['url']} 可达" + (f" · 固件 {ver}" if ver else "")}
        except GatewayError as e:
            gw_result = {"ok": False, "message": str(e)[:200]}
            problems.append("网关")

    ota_result = {"ok": False, "message": "未配置 OTA 服务器"}
    if cfg.get("ota", {}).get("url"):
        ok, data = OtaChecker(cfg["ota"]).fetch_manifest()
        if ok:
            ota_result = {"ok": True, "message": f"{cfg['ota']['url']} manifest 正常"}
        else:
            ota_result = {"ok": False, "message": str(data)[:200]}

    return {"ok": not problems, "error": "; ".join(problems) or None,
            "llm": llm_result, "gateway": gw_result, "ota": ota_result,
            "problems": problems}


# ---------- 提示词 API ----------

@app.get("/api/prompt")
def get_prompt():
    c = _load_raw()
    custom = (c["llm"].get("system_prompt") or "").strip()
    kb_index = knowledge.index_text()
    default_text = prompts.SYSTEM_PROMPT_TEMPLATE.replace("{KB_INDEX}", kb_index)
    effective = custom.replace("{KB_INDEX}", kb_index) if custom else default_text
    edit_text = custom if custom else prompts.SYSTEM_PROMPT_TEMPLATE
    return {"ok": True, "is_custom": bool(custom), "effective": effective,
            "edit_text": edit_text}


@app.post("/api/prompt")
async def save_prompt(request: Request):
    body = await request.json()
    if body.get("reset"):
        raw = _load_raw()
        raw["llm"]["system_prompt"] = ""
        _save_raw(raw)
        return {"ok": True, "is_custom": False}
    text = str(body.get("text") or "").strip()
    if len(text) < 50:
        return _err(400, "提示词太短（至少 50 字符），恢复默认请用「恢复默认模板」按钮")
    if "{KB_INDEX}" not in text:
        return _err(400, "缺少 {KB_INDEX} 占位符：运行时将无法注入知识库目录，请补回后再保存")
    raw = _load_raw()
    raw["llm"]["system_prompt"] = text
    _save_raw(raw)
    return {"ok": True, "is_custom": True}


# ---------- 快捷体检 ----------

@app.get("/api/healthcheck")
def healthcheck():
    try:
        cfg = load_config(require_llm=False)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if not cfg["gateway"].get("url"):
        return {"ok": False, "error": "未配置网关地址"}
    try:
        gw = Gateway(cfg["gateway"])
        gw.auth()
    except GatewayError as e:
        return {"ok": False, "error": str(e)}
    results = []
    for qid in ("system/health", "system/resources", "alarms/stats", "ota/status"):
        ok, payload = gw.query(qid)
        results.append({"query": qid, "ok": ok,
                        "summary": _summarize_health(qid, payload)})
    return {"ok": all(r["ok"] for r in results), "results": results}


def _summarize_health(qid, payload):
    if not isinstance(payload, (dict, list)):
        return str(payload)[:200]
    try:
        if qid == "alarms/stats" and isinstance(payload, dict):
            return json.dumps(payload, ensure_ascii=False)[:300]
        return json.dumps(payload, ensure_ascii=False)[:400]
    except (TypeError, ValueError):
        return str(payload)[:200]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8766)
