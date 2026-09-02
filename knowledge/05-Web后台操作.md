# Web 后台操作

> 来源：《EdgeOS 网关使用手册》《EdgeOS_WEB后台规划》

浏览器访问 `http://<网关IP>/`，admin 账号登录（首次强制改密）。

## 功能模块速览

| 模块 | 功能要点 |
|------|----------|
| 仪表盘 | 设备总数/活跃/离线统计、告警数量、系统资源概览、ECharts 实时趋势 |
| 告警中心 | 告警列表、确认/消除、阈值规则管理，WebSocket 实时刷新 |
| 实例列表 | 全协议设备统一管理（Zigbee/Modbus/MQTT/TCP/GPIO/Relay/内置），进详情页看属性/能力/控制/设备影子 |
| 产品库 | 品类/品牌/产品三层模型，从产品创建设备，模板绑定（Merge/Replace 双模式） |
| 空间管理 | 房间/区域组织设备，用于场景与告警归属 |
| MQTT | Broker 管理（1883 状态/客户端列表/订阅发布测试）+ 对外请求（云客户端配置） |
| 屏端显示 | 预览/模拟 4 英寸 LVGL 屏端（5-tile），仅操作已上屏（hmi_visible=true）设备 |
| 自动化 | 规则（条件-动作引擎）+ 场景（一键批量执行） |
| 用户管理 | RBAC 三级权限、创建用户、改密、生成对接令牌（仅 admin） |
| 调试中心 | 驱动诊断、任务/资源监控等工程视图 |

## 告警：确认 ≠ 消除（高频疑问）

| 操作 | 语义 |
|------|------|
| **确认** | 标记"已读"，消除未读数，历史保留 |
| **消除** | 从活跃列表移除并**删除数据库记录** |

新告警/确认/消除通过 WebSocket 实时推送刷新。

## 系统设置页签

| 页签 | 功能 |
|------|------|
| Zigbee 网络 | 网络状态、PAN ID、信道、固件版本、Permit Join 配网 |
| NTP/时区 | 时间同步配置（默认 CST-8，以太网连接后 SNTP 自动启动） |
| TCP Server | 事件推送服务（默认 8888）开关与端口 |
| TCP Client | Modbus TCP 客户端配置（目标主机/端口/从站 ID） |
| 有线网络 | 网关 IP 信息、静态 IP / DHCP 切换 |
| OTA 升级 | 检查更新 / 升级 / 确认版本 / 回滚（详见 OTA 主题） |
| 系统日志 | Web 日志中心（四类日志统一，7 天轮转） |

## 运行时配置体系

- **NVS 运行时配置**（config_mgr，Web 系统设置修改）：MQTT Broker/Client 参数、NTP/时区、TCP 端口、Modbus 串口参数（热切）、OTA 服务器 IP、静态 IP/DHCP
- **业务数据**（SQLite）：devices/rules/scenes/spaces/users/alarms/thresholds/history/logs/product_lib 系列等 15+ 张表，运行期读写全走 PSRAM + WriteBack 异步落库
- **编译期配置**：sdkconfig.defaults（如 CONFIG_MQTT_TASK_STACK_SIZE=32768）

## Web 前端运维

- 前端为 Vue3 单页应用，构建产物 48 chunk 打包进 LittleFS 镜像（spiffs_data/www）
- 更新 Web 页面：build_web.bat → 重新打包烧录，或走 OTA storage 组件（推荐，双槽免丢数据）
- 后端限制：HTTP 并发 socket 10、URI 256B、每连接 12KB 栈 —— 高并发场景注意
