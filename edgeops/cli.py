"""CLI 子命令：test / ask / kb / web。"""

import argparse
import json
import sys


def cmd_test(args):
    from .config import load_config, ConfigError
    from .gateway import Gateway, GatewayError
    from .llm import LLMClient, LLMError
    from .tools import OtaChecker
    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"✗ {e}")
        return 1
    ok_all = True
    try:
        client = LLMClient(cfg["llm"])
        reply = client.chat("你是连通性测试端点。只回答两个字母: OK", "ping", retries=0)
        print(f"✓ LLM {cfg['llm']['model']}: {(reply or '').strip()[:40]}")
    except LLMError as e:
        ok_all = False
        print(f"✗ LLM: {e}")
    if cfg["gateway"].get("url"):
        try:
            gw = Gateway(cfg["gateway"])
            gw.auth()
            ok, info = gw.query("system/info")
            ver = (info or {}).get("version", "") if ok else ""
            print(f"✓ 网关 {cfg['gateway']['url']}" + (f" 固件 {ver}" if ver else ""))
        except GatewayError as e:
            ok_all = False
            print(f"✗ 网关: {e}")
    else:
        print("⚠ 未配置网关地址")
    if cfg.get("ota", {}).get("url"):
        ok, data = OtaChecker(cfg["ota"]).fetch_manifest()
        print(f"{'✓' if ok else '✗'} OTA {cfg['ota']['url']}: "
              f"{'manifest 正常' if ok else data}")
        ok_all = ok_all and ok
    return 0 if ok_all else 1


def cmd_ask(args):
    from .config import load_config, ConfigError
    from .agent import run, AgentError
    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"✗ {e}")
        return 1
    question = " ".join(args.question) if isinstance(args.question, list) else args.question
    try:
        res = run(question, cfg, progress=lambda m: print(f"  {m}", file=sys.stderr))
    except AgentError as e:
        print(f"✗ {e}")
        return 1
    print(res["answer"])
    if args.verbose:
        print("\n--- 诊断计划 ---", file=sys.stderr)
        print(json.dumps(res["plan"], ensure_ascii=False, indent=2), file=sys.stderr)
    return 0


def cmd_kb(args):
    from . import knowledge
    if args.topic:
        doc = knowledge.get_doc(args.topic)
        if doc is None:
            print(f"✗ 主题不存在: {args.topic}")
            return 1
        print(doc)
        return 0
    for t in knowledge.topics():
        print(f"{t['id']:<18} {t['title']}")
        print(f"{'':<18} {t['summary']}")
    return 0


def cmd_search(args):
    from . import knowledge
    query = " ".join(args.query)
    hits = knowledge.search(query, top=args.top)
    if not hits:
        print("（无命中）")
        return 0
    all_topics = {t["id"]: t for t in knowledge.topics()}
    for tid in hits:
        t = all_topics[tid]
        print(f"{tid:<18} {t['title']} —— {t['summary']}")
    return 0


def cmd_web(args):
    import uvicorn
    from .webui import app
    print(f"EdgeOps 运维助手 Web UI: http://{args.host}:{args.port}  (Ctrl+C 退出)")
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="edgeops",
                                     description="EdgeOS 网关 AI 运维助手")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("test", help="测试 LLM / 网关 / OTA 连通性")
    p.set_defaults(func=cmd_test)

    p = sub.add_parser("ask", help="命令行提问")
    p.add_argument("question", nargs="+", help="运维问题")
    p.add_argument("-v", "--verbose", action="store_true", help="打印诊断计划")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("kb", help="列出/查看运维知识库")
    p.add_argument("topic", nargs="?", help="主题 id（省略则列出目录）")
    p.set_defaults(func=cmd_kb)

    p = sub.add_parser("search", help="知识库关键词检索")
    p.add_argument("query", nargs="+")
    p.add_argument("--top", type=int, default=3)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("web", help="启动 Web 服务")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8766)
    p.set_defaults(func=cmd_web)

    args = parser.parse_args(argv)
    return args.func(args)
