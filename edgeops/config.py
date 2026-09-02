import copy
import json
import os

DEFAULTS = {
    "llm": {
        "base_url": "",
        "api_key": "",
        "model": "",
        "temperature": 0.2,
        "max_tokens": 16384,
        "timeout": 180,
        "json_mode": True,
        "system_prompt": "",
    },
    "gateway": {
        "url": "",
        "token": "",
    },
    "ota": {
        "url": "",
        "token": "",
    },
    "agent": {
        "max_kb_topics": 5,
        "max_gateway_calls": 8,
        "history_rounds": 6,
        "gw_result_chars": 3000,
        "kb_chars_per_doc": 9000,
    },
}

ENV_MAP = {
    "EDGEOPS_LLM_BASE_URL": ("llm", "base_url"),
    "EDGEOPS_LLM_API_KEY": ("llm", "api_key"),
    "EDGEOPS_LLM_MODEL": ("llm", "model"),
    "EDGEOPS_GATEWAY_URL": ("gateway", "url"),
    "EDGEOPS_GATEWAY_TOKEN": ("gateway", "token"),
    "EDGEOPS_OTA_URL": ("ota", "url"),
}


class ConfigError(Exception):
    pass


def _merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def config_search_paths(explicit=None):
    paths = []
    if explicit:
        paths.append(os.path.abspath(explicit))
    env_path = os.environ.get("EDGEOPS_CONFIG")
    if env_path:
        paths.append(os.path.abspath(env_path))
    paths.append(os.path.join(os.getcwd(), "config.json"))
    paths.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json"))
    paths.append(os.path.join(os.path.expanduser("~"), ".edgeops", "config.json"))
    return paths


def _env_config_path():
    p = os.environ.get("EDGEOPS_CONFIG")
    return os.path.abspath(p) if p else None


def load_config(explicit=None, require_llm=True):
    cfg_path = _env_config_path()
    if not cfg_path:
        for p in config_search_paths(explicit):
            if os.path.isfile(p):
                cfg_path = p
                break
        else:
            cfg_path = None
    data = {}
    if cfg_path and os.path.isfile(cfg_path):
        with open(cfg_path, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                raise ConfigError(f"配置文件 JSON 解析失败 {cfg_path}: {e}")
    elif explicit and not os.path.isfile(explicit):
        raise ConfigError(f"找不到配置文件: {explicit}")

    cfg = _merge(DEFAULTS, data)
    for env_key, (section, key) in ENV_MAP.items():
        val = os.environ.get(env_key)
        if val:
            cfg[section][key] = val

    if require_llm:
        missing = []
        if not cfg["llm"]["base_url"]:
            missing.append("llm.base_url")
        if not cfg["llm"]["api_key"]:
            missing.append("llm.api_key")
        if not cfg["llm"]["model"]:
            missing.append("llm.model")
        if missing:
            raise ConfigError(
                "缺少 LLM 配置: " + ", ".join(missing)
                + "。请在 config.json 配置或设置环境变量 EDGEOPS_LLM_*（参考 config.example.json）"
            )
    cfg["_config_path"] = cfg_path
    return cfg
