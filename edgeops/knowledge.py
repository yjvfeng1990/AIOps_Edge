"""运维知识库：kb.json 索引 + Markdown 主题文档，关键词评分检索。"""

import json
import os
import re
import threading
from pathlib import Path

KB_DIR = Path(__file__).resolve().parent.parent / "knowledge"

_LOCK = threading.Lock()
_CACHE = None


class KnowledgeError(Exception):
    pass


def _load():
    global _CACHE
    with _LOCK:
        if _CACHE is not None:
            return _CACHE
        index_path = KB_DIR / "kb.json"
        if not index_path.is_file():
            raise KnowledgeError(f"知识库索引不存在: {index_path}")
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise KnowledgeError(f"知识库索引解析失败: {e}")
        topics = {}
        for t in index.get("topics", []):
            tid = t.get("id")
            f = KB_DIR / t.get("file", "")
            if tid and f.is_file():
                t["_path"] = str(f)
                topics[tid] = t
        _CACHE = {"index": index, "topics": topics}
        return _CACHE


def topics():
    """返回 [{id,title,summary,keywords}]（不含文档正文）。"""
    kb = _load()
    return [{k: t.get(k) for k in ("id", "title", "summary", "keywords")}
            for t in kb["topics"].values()]


def index_text():
    """生成供 LLM 选择的知识目录文本。"""
    lines = []
    for t in topics():
        lines.append(f"- {t['id']}: {t['title']} —— {t['summary']}")
    return "\n".join(lines)


def get_doc(topic_id, max_chars=9000):
    """读取主题文档全文（超长截断）。"""
    kb = _load()
    t = kb["topics"].get(topic_id)
    if not t:
        return None
    try:
        text = Path(t["_path"]).read_text(encoding="utf-8")
    except OSError:
        return None
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…（文档超长已截断）"
    return text


def _tokenize(query):
    return [w for w in re.split(r"[\s,，。？！、；：;:?!/\\()\[\]{}\"']+", query.lower()) if w]


def search(query, top=3):
    """关键词评分检索：命中 keywords×3 + title×2 + summary×1；支持短词包含匹配。"""
    kb = _load()
    tokens = _tokenize(query)
    if not tokens:
        return []
    scored = []
    for t in kb["topics"].values():
        score = 0
        hay_kw = " ".join(t.get("keywords", [])).lower()
        hay_title = t.get("title", "").lower()
        hay_sum = t.get("summary", "").lower()
        for tok in tokens:
            if len(tok) >= 2:
                if tok in hay_kw:
                    score += 3
                if tok in hay_title:
                    score += 2
                if tok in hay_sum:
                    score += 1
            # 中文整句：任一 keyword 出现在 query 中
        for kw in t.get("keywords", []):
            if len(kw) >= 2 and kw.lower() in query.lower():
                score += 2
        if score > 0:
            scored.append((score, t["id"]))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [tid for _, tid in scored[:top]]


def resolve_topic_ids(ids, query=None, max_topics=5):
    """校验并归一 LLM 规划出的主题列表：合法 id 优先，空则按 query 检索兜底。"""
    kb = _load()
    out = []
    for i in ids or []:
        if i in kb["topics"] and i not in out:
            out.append(i)
    if not out and query:
        out = search(query, top=min(3, max_topics))
    return out[:max_topics]
