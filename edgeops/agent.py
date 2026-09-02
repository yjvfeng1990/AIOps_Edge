"""Agent 编排：诊断规划（JSON）→ 材料收集（代码执行）→ 流式回答。"""

import time

from . import knowledge, prompts, tools
from .gateway import Gateway
from .llm import LLMClient, LLMError, extract_json


class AgentError(Exception):
    pass


class TaskCancelled(Exception):
    pass


def run(question, cfg, history=None, progress=None, llm_live=None,
        cancel_check=None):
    """执行一轮运维问答。

    返回 dict: {plan, tool_log, answer}
    progress(msg) 阶段日志回调；llm_live 为 dict，收集流式 reasoning/content。
    """
    def log(msg):
        if progress:
            progress(msg)

    def cancelled():
        return bool(cancel_check and cancel_check())

    agent_cfg = cfg.get("agent", {})
    max_kb = int(agent_cfg.get("max_kb_topics", 5))
    max_gw = int(agent_cfg.get("max_gateway_calls", 8))
    history_rounds = int(agent_cfg.get("history_rounds", 6))

    client = LLMClient(cfg["llm"])
    kb_index = knowledge.index_text()

    # ---- 阶段 1：诊断规划 ----
    log("分析问题，规划诊断材料…")
    plan = {}
    try:
        plan_raw = client.chat(
            prompts.build_plan_prompt(kb_index, tools.gw_queries_text()),
            question, retries=1)
        plan = extract_json(plan_raw)
        if not isinstance(plan, dict):
            plan = {}
    except LLMError as e:
        log(f"⚠ 规划模型输出异常（降级为关键词检索）: {str(e)[:120]}")
        plan = {}

    kb_topics = knowledge.resolve_topic_ids(plan.get("kb_topics"),
                                            query=question, max_topics=max_kb)
    gw_calls = [q for q in (plan.get("gateway_calls") or [])
                if q in tools.GW_QUERIES][:max_gw]
    check_ota = bool(plan.get("check_ota")) and bool(cfg.get("ota", {}).get("url"))
    plan = {"kb_topics": kb_topics, "gateway_calls": gw_calls,
            "check_ota": check_ota, "reason": str(plan.get("reason") or "")[:200]}

    if cancelled():
        raise TaskCancelled()

    for tid in kb_topics:
        log(f"📖 检索知识库: {tid}")
    if gw_calls:
        log(f"🔌 查询网关: {', '.join(gw_calls)}")
    if check_ota:
        log("📦 检查 OTA 服务器 manifest")

    # ---- 阶段 2：材料收集 ----
    materials, tool_log = tools.collect_materials(plan, cfg)

    if cancelled():
        raise TaskCancelled()

    # ---- 阶段 3：综合回答（流式） ----
    log("🧠 综合材料生成回答…")
    system = prompts.build_system_prompt(kb_index,
                                         custom=cfg["llm"].get("system_prompt", ""))
    user_parts = [f"## 用户问题\n{question}"]
    if history:
        user_parts.append("## 对话历史（最近几轮，供理解上下文）\n" + history)
    user_parts.append(f"## 参考材料\n{materials}")
    user_parts.append("请依据上述材料回答用户问题。若材料中有网关实时数据，结论要引用具体数据。")
    user_content = "\n\n".join(user_parts)

    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user_content}]

    def on_delta(kind, text):
        if llm_live is not None:
            llm_live.setdefault(kind, []).append(text)

    # 回答阶段用自然语言 Markdown：JSON 输出模式仅用于阶段1的诊断规划。
    # 若不关闭，模型会把回答包成 {"role": ...} 之类的 JSON 对象。
    client.json_mode = False
    answer = client.chat_messages(messages, retries=1, on_delta=on_delta)
    log("✓ 回答完成")
    return {"plan": plan, "tool_log": tool_log, "answer": answer}


def build_history(messages, rounds=6):
    """把会话历史压成文本（供第三阶段上下文）。"""
    recent = [m for m in messages if m.get("role") in ("user", "assistant")
              and m.get("content")][-rounds:]
    lines = []
    for m in recent:
        role = "用户" if m["role"] == "user" else "助手"
        lines.append(f"[{role}] {str(m['content'])[:800]}")
    return "\n".join(lines) if lines else None


def quick_check(cfg, gateway=None):
    """一键体检：健康 + 资源 + 告警 + OTA 状态（不走 LLM，直接采集）。"""
    out = []
    gw = gateway or (Gateway(cfg["gateway"]) if cfg.get("gateway", {}).get("url") else None)
    if gw is None:
        return None
    for qid in ("system/health", "system/resources", "alarms/stats", "ota/status"):
        ok, payload = gw.query(qid)
        out.append((qid, ok, payload))
    return out
