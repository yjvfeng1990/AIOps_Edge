# API 与 MQTT 对接

> 来源：《appendix-a-rest-api-spec.md》(v2.8)、《appendix-b-mqtt-topic-spec.md》(v1.6)

## REST 鉴权

1. **登录换 Token**（24h 过期）：
   ```bash
   curl -X POST http://<IP>/api/v1/auth/login \
     -H "Content-Type: application/json" \
     -d '{"username":"admin","password":"<密码>"}'
   # 响应 {code:0, data:{token:"..."}}
   ```
2. **永久对接令牌**（推荐对接用）：Web 后台 → 用户管理 → 生成 API Token（NVS 存储，admin 权限）；也可 `GET /api/v1/auth/api-token` 查看
3. 后续请求带 `Authorization: Bearer <token>`
4. 统一响应格式：`{code: 0, message: "...", data: ...}`，code≠0 为业务错误
5. WebSocket：`ws://<IP>/ws?token=xxx`

## 常用运维端点（只读）

| 类别 | 端点 |
|------|------|
| 系统 | `/api/v1/system/info`、`/health`、`/health/components`、`/resources`、`/tasks`、`/audit` |
| 设备 | `GET /api/v1/devices`、`GET /api/v1/devices/{uuid}` |
| 告警 | `GET /api/v1/alarms`、`/alarms/stats` |
| 日志 | `GET /api/v1/logs`、`/logs/download` |
| OTA | `GET /api/v1/ota/status`、`/check`、`/storage`、`/fonts`、`/database` |
| MQTT | `GET /api/v1/mqtt/stats`、`/mqtt/client/status`、`/mqtt/topics` |
| Zigbee | `GET /api/v1/zigbee/network`、`/zigbee/devices` |
| 产品库 | `GET /api/v1/products`、`/categories`、`/capabilities`、`/manufacturers` |
| 自动化 | `GET /api/v1/rules`、`/scenes`、`/spaces`、`/history` |

写操作端点（POST/PUT/DELETE）均有审计；OTA 升级类：`POST /api/v1/ota/upgrade|confirm|rollback`。

## MQTT 主题树（内置 Broker 1883）

| 主题 | 方向 | Payload |
|------|------|---------|
| `device/{uuid}/property` | 设备→总线 | 属性上报 JSON |
| `device/{uuid}/state` | 设备→总线 | 在线/离线 |
| `device/{uuid}/set/{property}` | 总线→设备 | 控制，如 `{"power":true}` |
| `edge/...` | 云桥接 | 云端同步通道 |
| `alarm/new` | 系统→订阅者 | 新告警 |

mosquitto 示例：

```bash
# 订阅全部设备属性
mosquitto_sub -h <IP> -p 1883 -t 'device/+/property' -v

# 控制设备（power 属性）
mosquitto_pub -h <IP> -p 1883 -t 'device/<uuid>/set/power' -m '{"power":true}'
```

**Broker 约束**：无认证/无 TLS/无 ACL（仅限可信局域网）、64 客户端、主题 128B、Payload 4KB、16 订阅/客户端、QoS 0-1（不支持 QoS2）、支持 Retain 与通配符。

## TCP Server（8888）

- JSON + 换行 分帧，最大 8 客户端
- 推送设备/系统/自动化/配网事件，适合第三方系统集成
- 开关与端口：系统设置 → TCP Server

## 对接注意事项

- HTTP 服务器限制：并发 socket 10、URI 256B、每连接 12KB 栈 → 轮询频率不宜过密，优先用 WebSocket/TCP 8888 订阅推送
- 登录 Token 24h 过期，对接程序建议用永久令牌并做好 401 重登
- 所有时间戳注意 NTP 同步状态（未联网时间不可靠）
