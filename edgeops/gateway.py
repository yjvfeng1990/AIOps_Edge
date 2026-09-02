"""EdgeOS 网关运维 REST 客户端。

仅暴露只读运维查询白名单（系统健康/资源/任务/设备/告警/日志/OTA/MQTT/Zigbee），
避免 Agent 误触发写操作；所有写操作引导用户去 Web 后台执行。
"""

import requests


class GatewayError(Exception):
    pass


# query_id -> (method, path, 用途说明)
GW_QUERIES = {
    "system/info":             ("GET", "/api/v1/system/info", "系统信息：固件版本、编译时间、IP、运行时长"),
    "system/health":           ("GET", "/api/v1/system/health", "系统健康总览（health_mgr，OK/DEGRADED/CRITICAL）"),
    "system/health/components": ("GET", "/api/v1/system/health/components", "24 槽位组件健康明细"),
    "system/resources":        ("GET", "/api/v1/system/resources", "系统资源：内存/PSRAM 水位（5s 采集）"),
    "system/tasks":            ("GET", "/api/v1/system/tasks", "FreeRTOS 任务列表与栈水位"),
    "system/timers":           ("GET", "/api/v1/system/timers", "定时器列表"),
    "system/audit":            ("GET", "/api/v1/system/audit", "审计日志（关键操作记录）"),
    "devices":                 ("GET", "/api/v1/devices", "全部设备实例列表（含在线状态）"),
    "drivers":                 ("GET", "/api/v1/drivers", "已注册驱动列表"),
    "drivers/diag":            ("GET", "/api/v1/drivers/diag", "全部驱动诊断统计（读写/错误/延迟）"),
    "alarms":                  ("GET", "/api/v1/alarms", "活跃告警列表"),
    "alarms/stats":            ("GET", "/api/v1/alarms/stats", "告警统计"),
    "logs":                    ("GET", "/api/v1/logs", "系统日志（Web 日志中心，支持分页/级别过滤）"),
    "history":                 ("GET", "/api/v1/history", "属性历史记录"),
    "ota/status":              ("GET", "/api/v1/ota/status", "OTA 当前状态与版本信息"),
    "ota/check":               ("GET", "/api/v1/ota/check", "向 OTA 服务器检查更新"),
    "ota/storage":             ("GET", "/api/v1/ota/storage", "Web UI storage 组件版本/槽位状态"),
    "ota/fonts":               ("GET", "/api/v1/ota/fonts", "字库组件版本状态"),
    "ota/database":            ("GET", "/api/v1/ota/database", "数据库组件版本状态"),
    "mqtt/stats":              ("GET", "/api/v1/mqtt/stats", "内置 MQTT Broker 统计（客户端/收发）"),
    "mqtt/client/status":      ("GET", "/api/v1/mqtt/client/status", "云端 MQTT 客户端连接状态"),
    "mqtt/topics":             ("GET", "/api/v1/mqtt/topics", "MQTT 主题列表"),
    "zigbee/network":          ("GET", "/api/v1/zigbee/network", "Zigbee 网络状态（信道/PAN/版本）"),
    "zigbee/devices":          ("GET", "/api/v1/zigbee/devices", "Zigbee 子设备列表"),
    "rules":                   ("GET", "/api/v1/rules", "自动化规则列表"),
    "scenes":                  ("GET", "/api/v1/scenes", "场景列表"),
    "spaces":                  ("GET", "/api/v1/spaces", "空间（房间/区域）列表"),
    "notifications":           ("GET", "/api/v1/notifications", "通知中心列表"),
}


class Gateway:
    """Token-only 对接：使用 Web 后台生成的永久 API Token（NVS 存储，admin 权限）。"""

    def __init__(self, gw_cfg, timeout=10):
        self.base_url = (gw_cfg.get("url") or "").rstrip("/")
        self.token = gw_cfg.get("token") or ""
        self.timeout = timeout

    @property
    def configured(self):
        return bool(self.base_url)

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def request(self, method, path, body=None, params=None):
        url = self.base_url + path
        try:
            resp = requests.request(method, url, headers=self._headers(),
                                    json=body, params=params, timeout=self.timeout)
        except requests.ConnectionError:
            raise GatewayError(f"无法连接网关 {self.base_url}：请检查网线/IP，或网关是否在线")
        except requests.Timeout:
            raise GatewayError(f"网关请求超时: {path}")
        try:
            data = resp.json()
        except ValueError:
            raise GatewayError(f"网关返回非 JSON (HTTP {resp.status_code}): {resp.text[:200]}")
        code = data.get("code", -1)
        if resp.status_code != 200 or code != 0:
            msg = data.get("message", resp.text[:200])
            if resp.status_code == 401 or code == 401:
                raise GatewayError(
                    "网关对接令牌无效或已过期：请在 Web 后台 → 用户管理 → 生成 API Token，"
                    "更新到「对接配置」后重试"
                )
            raise GatewayError(f"网关错误 (HTTP {resp.status_code}, code={code}): {msg}")
        return data.get("data")

    def auth(self):
        """校验对接令牌（探活 profile），未配置直接报错。不做账号密码登录。"""
        if not self.base_url:
            raise GatewayError("未配置网关地址")
        if not self.token:
            raise GatewayError(
                "未配置网关对接令牌：请在 Web 后台 → 用户管理 → 生成 API Token，"
                "填入「对接配置」"
            )
        self.request("GET", "/api/v1/auth/profile")
        return True

    def query(self, query_id):
        """按白名单 ID 执行只读查询，返回 (ok, payload_or_error)。"""
        item = GW_QUERIES.get(query_id)
        if not item:
            return False, f"未知查询 {query_id}（不在白名单内）"
        method, path = item[0], item[1]
        try:
            return True, self.request(method, path)
        except GatewayError as e:
            return False, str(e)
