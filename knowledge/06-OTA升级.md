# OTA 升级

> 来源：《EdgeOS 网关使用手册》第 4 章、《edgeos-ota-server-deployment.md》

## 四组件升级模型

| 组件 | 分区 | 升级方式 | 回滚 |
|------|------|----------|------|
| firmware | ota_0/ota_1 双槽 6MB×2 | 下载到备用槽 → 重启切换 | ✅ 崩溃自动回滚 + 手动回滚按钮 |
| storage（Web UI） | storage_0/1 双槽 3MB×2 | 下载非活跃槽 → 标记切换 → 重启生效 | ✅ 槽位切换 |
| fonts（字库） | fonts 单区 4MB | 覆盖式写入 + NVS 版本记录 | ❌ 无回滚 |
| database | database 单区 2MB | 覆盖式写入 + NVS 版本记录 | ❌ 无回滚 |

## 版本检测规则

- **固件**：比较语义版本 `x.y.z.w`（忽略 v 前缀与 git 后缀）；版本相同但**编译时间戳（build）不同**也判定有更新
- **组件**（storage/fonts/database）：比较版本号 + **MD5**；版本相同但 MD5 不同判定有更新
- 首次串口直刷的固件 NVS 无 build 记录，不会误报升级
- ⚠️ fonts/database 覆盖写无回滚，升级前确认数据已备份

## 从 Web 后台升级（标准流程）

1. 系统设置 → OTA 升级
2. （可选）确认 OTA 服务器地址，默认 `http://10.10.0.6:8080`，manifest 路径 `/ota/manifest.json`
3. 点 **检查更新** → 显示当前版本/服务器版本/更新说明
4. 点 **升级** → 下载到备用槽 → 自动重启
5. 重启后观察运行正常 → 点 **确认当前版本**

## 确认当前版本（重要机制）

- ESP-IDF 双槽回滚：新固件默认"待确认"状态
- **确认** = `esp_ota_mark_app_valid_cancel_rollback()`，标记当前固件有效
- 未确认时若新固件崩溃/看门狗复位 → **自动回滚旧槽**（保护机制，非故障）
- 最佳实践：观察正常后再确认；一旦确认，崩溃不再自动回滚
- API：`POST /api/v1/ota/confirm`；回滚：`POST /api/v1/ota/rollback`

## 发布新固件到 OTA 服务器

```bat
:: 1. 构建（版本号来自 git tag，先 commit 后 tag）
git add . && git commit -m "feat: xxx"
git tag v1.0.0.1
build_p4.bat

:: 2. 生成 manifest（自动提取 version/build/size；组件自动算 md5）
gen_manifest.bat firmware  --file build\zigbeetomqtt.bin --notes "Change notes"
gen_manifest.bat component --name storage  --file build\storage_0.bin --version 1.0.0 --rename storage.bin
gen_manifest.bat component --name fonts    --file build\fonts.bin    --version 1.0.0
gen_manifest.bat component --name database --file build\database.bin --version 1.0.0

:: 3. 查看生成结果
gen_manifest.bat show
```

然后将 manifest.json + 各 bin 上传到服务器对应目录（manifest.json 结构见下方服务器目录）。`--notes` 建议用英文，避免 cmd 代码页传参乱码。

## OTA 服务器部署（10.10.0.6 Ubuntu 24.04）

```
服务器根目录 (8080)
├── ota/
│   ├── manifest.json
│   ├── firmware/zigbeetomqtt.bin
│   ├── storage/storage.bin
│   ├── fonts/fonts.bin
│   └── database/database.bin
```

- Python systemd 服务（ota_server.py），端口 8080，Restart=always
- 上传 Token：`edgeos-ota-2024`（下载/manifest 读取不需要）
- API：`GET /ota/manifest.json`、`GET /ota/...`（下载）、`POST /api/upload`（上传）、`GET /healthz`

```bash
sudo systemctl status ota-server        # 状态
sudo systemctl restart ota-server       # 重启
journalctl -u ota-server -f             # 看日志
curl http://10.10.0.6:8080/ota/manifest.json | jq   # 验证 manifest
```

## OTA 相关风险（当前未实现）

- ❌ 固件 RSA-2048 签名验证未实施（升级包可被替换）
- ❌ 防降级版本检查未实施
- ❌ Recovery Mode 未实施
- ❌ C6 固件无 OTA 通道（只能串口刷）
- 服务器部署在局域网时注意：上传接口有 Token 但下载无鉴权
