# 故障排查 FAQ

> 来源：《使用手册》第 5 章、《LP_WDT_SDIO诊断与优化方案》《SDIO触发LP_WDT无线重启的解决思路》《CHANGE_LOG》

## 故障速查表

| 现象 | 处理 |
|------|------|
| 冷启动 `rst:0x10 CHIP_LP_WDT_RESET`（SDIO init 期间） | 已知 P4 硬件 bug（HP→LP 桥写入被缓冲丢失），已多重缓解；若复发见下方专项 |
| 升级后反复重启 | 新固件未"确认"时崩溃自动回滚是**保护机制非故障**；确认后仍异常 → 串口直刷恢复 |
| 无法访问 Web | 查 RJ45 链路 → 串口日志看 IP → 确认同网段；GPIO35 为 RMII 专用不可挪用 |
| 首次登录要求改密 | 默认 admin/admin123，首次登录强制修改，正常流程 |
| 告警"确认"后仍显示 | 确认≠消除；点**消除**才删除记录 |
| OTA 提示有更新但版本号相同 | 固件 build 时间戳或组件 MD5 与服务器不同，属正常变更检测 |
| 中文显示方块/缺字 | `gen_fonts.bat` + `flash_fonts.bat`，或 OTA fonts 组件 |
| C6 无法进下载模式 | 按住 BOOT → 按一下 RESET 松开 → 松开 BOOT |
| 设备时间不准 | 系统设置 → NTP/时区；未联网时定时规则在错误时间轴运行 |
| 定时规则不触发 | #194/#195 已修复（整分钟匹配 + 分钟去重 + SMP 竞态）；仍需确认 NTP 已同步且规则启用 |
| Task Watchdog 告警（evt_dispatch） | 已 pinning CPU1 + 32 事件批处理缓解，持续告警则抓任务列表分析 |
| mqtt_task 栈保护崩溃 | #196/#197 已修复（栈 32KB + 日志钩子静态全局 + 互斥串行化） |
| 熄屏误唤醒 | #198 已修复 |
| MQTT 客户端收不到消息 | 检查 QoS ≤1（不支持 QoS2）、订阅数 ≤16/客户端、Payload ≤4KB、主题 ≤128B |
| Modbus 读不到数据 | 驱动诊断看错误计数；查从站地址/波特率/8N1/接线 A/B、485 共地 |

## LP_WDT / SDIO 专项（rst:0x10）

**根因链**（详见 DOCS 两份专项文档）：
1. SDIO init 期间 ROM flashboot 模式 LP_WDT ~5.6s 超时
2. P4 硬件 bug：HP→LP 桥写入被缓冲丢失（读回正常但硬件未生效）

**已实施缓解**：
- 喂狗间隔 200ms→50ms，SDIO 关键步骤间让出 CPU
- RX 任务循环喂狗 + LP_WDT 双重保险
- C6 STREAM 模式改 PACKET 模式、SDIO 降频 5MHz、RX 纯轮询

**复发时诊断步骤**：
1. 串口确认复位码 `rst:0x10 (CHIP_LP_WDT_RESET)`
2. 读 LP_WDT_INT_RAW 寄存器判断触发源
3. 对照 rcp_sdio.c 诊断统计（错误计数/心跳/reinit 次数）
4. 后备方案：LP core 协处理器喂狗

## 设备离线排查路径

1. `GET /api/v1/system/health` → 看整体健康
2. `GET /api/v1/devices` → 找到目标设备看 online 状态与最后上报时间
3. 按协议分流：
   - **Zigbee**：`GET /api/v1/zigbee/network` 看信道/PAN；子设备可能休眠/离网，重新配网（Permit Join）
   - **Modbus**：`GET /api/v1/drivers/diag` 看错误计数；查串口参数与接线
   - **MQTT/TCP**：看客户端连接状态
4. `GET /api/v1/logs` 按时间线找异常日志

## 升级失败/升级后异常

1. `GET /api/v1/ota/status` 看当前槽位与版本
2. 未确认状态 → 等待自动回滚或手动 `POST /api/v1/ota/rollback`
3. 确认后异常 → 串口直刷：`flash_app.bat`
4. Web 页面异常 → OTA storage 组件重新升级（双槽切换，不丢数据库）
