"""运维工具层：OTA manifest 检查 + 网关查询结果摘要化。"""

import json

import requests

from .gateway import Gateway, GW_QUERIES


class OtaChecker:
    def __init__(self, ota_cfg, timeout=8):
        self.url = (ota_cfg.get("url") or "").rstrip("/")
        self.token = ota_cfg.get("token") or ""
        self.timeout = timeout

    @property
    def configured(self):
        return bool(self.url)

    def fetch_manifest(self):
        """拉取 OTA 服务器 manifest，返回 (ok, manifest_or_error)。"""
        if not self.url:
            return False, "未配置 OTA 服务器地址"
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            resp = requests.get(f"{self.url}/ota/manifest.json",
                                headers=headers, timeout=self.timeout)
        except requests.ConnectionError:
            return False, f"无法连接 OTA 服务器 {self.url}"
        except requests.Timeout:
            return False, "OTA 服务器请求超时"
        if resp.status_code != 200:
            return False, f"OTA 服务器响应 HTTP {resp.status_code}"
        try:
            return True, resp.json()
        except ValueError:
            return False, "manifest.json 解析失败"

    def summarize(self, manifest, gateway_version=None):
        """把 manifest 压缩成 LLM 友好的摘要文本。"""
        lines = []
        for comp in ("firmware", "storage", "fonts", "database"):
            items = manifest.get(comp) or []
            for it in items[:1]:
                line = f"{comp}: v{it.get('version', '?')}"
                if it.get("build"):
                    line += f" (build {it['build']})"
                if it.get("release_date"):
                    line += f" 发布于 {it['release_date']}"
                if it.get("notes"):
                    line += f" —— {str(it['notes'])[:120]}"
                lines.append(line)
        if gateway_version:
            lines.append(f"网关当前固件版本: {gateway_version}")
        return "\n".join(lines) or "manifest 为空"


def gw_queries_text():
    """生成供 LLM 规划的网关查询清单文本。"""
    lines = [f"- {qid}: {desc}" for qid, (_, _, desc) in GW_QUERIES.items()]
    return "\n".join(lines)


def summarize_gw_result(query_id, payload, max_chars=3000):
    """网关查询结果 JSON 化 + 截断；列表类结果给出条数与首条样本。"""
    try:
        text = json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(payload)
    if isinstance(payload, list):
        head = f"[共 {len(payload)} 条]"
        text = head + text
    if len(text) > max_chars:
        text = text[:max_chars] + "…（结果超长已截断）"
    return text


def collect_materials(plan, cfg, gateway=None):
    """按 plan 收集材料，返回 (materials_text, tool_log)。

    tool_log 条目: {"type": "kb"|"gw"|"ota", "name", "ok", "detail"}
    """
    agent_cfg = cfg.get("agent", {})
    kb_chars = int(agent_cfg.get("kb_chars_per_doc", 9000))
    gw_chars = int(agent_cfg.get("gw_result_chars", 3000))
    tool_log = []
    parts = []

    # 1. 知识库文档
    for tid in plan.get("kb_topics") or []:
        from . import knowledge
        doc = knowledge.get_doc(tid, max_chars=kb_chars)
        if doc:
            title = knowledge.topics() and next(
                (t["title"] for t in knowledge.topics() if t["id"] == tid), tid)
            parts.append(f"### 知识库 · {title}\n\n{doc}")
            tool_log.append({"type": "kb", "name": tid, "ok": True,
                             "detail": f"已加载《{title}》({len(doc)} 字)"})
        else:
            tool_log.append({"type": "kb", "name": tid, "ok": False,
                             "detail": "文档不存在或读取失败"})

    # 2. 网关实时查询
    gw = gateway
    if plan.get("gateway_calls") and gw is None:
        gw = Gateway(cfg["gateway"]) if cfg.get("gateway", {}).get("url") else None
        if gw is None:
            tool_log.append({"type": "gw", "name": "-", "ok": False,
                             "detail": "未配置网关地址，跳过实时查询"})
    if gw is not None and plan.get("gateway_calls"):
        try:
            gw.auth()
        except Exception as e:
            tool_log.append({"type": "gw", "name": "auth", "ok": False,
                             "detail": str(e)[:200]})
            gw = None
    if gw is not None:
        gw_parts = []
        for qid in plan["gateway_calls"]:
            ok, payload = gw.query(qid)
            if ok:
                summary = summarize_gw_result(qid, payload, max_chars=gw_chars)
                gw_parts.append(f"#### {qid}\n{summary}")
                tool_log.append({"type": "gw", "name": qid, "ok": True,
                                 "detail": _brief(payload)})
            else:
                gw_parts.append(f"#### {qid}\n查询失败: {payload}")
                tool_log.append({"type": "gw", "name": qid, "ok": False,
                                 "detail": str(payload)[:200]})
        if gw_parts:
            parts.append("### 网关实时数据\n\n" + "\n\n".join(gw_parts))

    # 3. OTA manifest
    if plan.get("check_ota"):
        ota = OtaChecker(cfg.get("ota", {}))
        if not ota.configured:
            tool_log.append({"type": "ota", "name": "manifest", "ok": False,
                             "detail": "未配置 OTA 服务器地址"})
        else:
            ok, data = ota.fetch_manifest()
            if ok:
                gw_ver = _gateway_version(cfg) if gw else None
                parts.append("### OTA 服务器 manifest 摘要\n\n" + ota.summarize(data, gw_ver))
                tool_log.append({"type": "ota", "name": "manifest", "ok": True,
                                 "detail": ota.summarize(data).replace("\n", "; ")[:200]})
            else:
                tool_log.append({"type": "ota", "name": "manifest", "ok": False,
                                 "detail": str(data)[:200]})

    return ("\n\n".join(parts) if parts else "（本轮未收集到附加材料）"), tool_log


def _gateway_version(cfg):
    try:
        gw = Gateway(cfg["gateway"])
        gw.auth()
        ok, data = gw.query("system/info")
        if ok and isinstance(data, dict):
            return data.get("version") or data.get("firmware_version")
    except Exception:
        pass
    return None


def _brief(payload):
    """工具日志用的一句话摘要。"""
    try:
        if isinstance(payload, list):
            return f"返回 {len(payload)} 条记录"
        if isinstance(payload, dict):
            items = payload.get("items")
            if isinstance(items, list):
                return f"返回 {len(items)} 条记录"
            keys = list(payload.keys())[:6]
            return "字段: " + ", ".join(keys)
    except Exception:
        pass
    return "OK"
