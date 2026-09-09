# 文档与固件一致性记录

本仓库以 `doc/CURRENT_IMPLEMENTATION.md` 为事实基准，完整协议见 `doc/UART_COMMANDS.md`，移植说明见 `doc/PORTING.md`，生产安全流程见 `doc/SECURITY_PROVISIONING.md`。

## 本次安全架构更新

- 生产授权已从 TF 卡共享 `/eyecare.unlock` 改为 USB Serial-JTAG 单机授权。
- `READ_ID` 返回 eFuse MAC 与 NVS 16 字节设备序列号。
- `CHALLENGE` 生成一次性随机 nonce；`ACTIVATE` 的 P-256 签名绑定 nonce、MAC、序列号。
- 验签成功后才写入 `EYECARE_UNLOCKED` eFuse；失败计数持久化到 NVS，达到阈值后返回 `ERR_LOCKED`。
- `unlock_token.py` 与 `unlock_provision.sh` 已按新帧格式更新；不再向 TF 卡写入解锁文件。
- 开发配置仍关闭 Secure Boot、Flash Encryption 和生产锁定，避免误烧开发板。

## 维护规则

修改安全协议、引脚、外设链路或启动行为时，同时更新事实表、UART 文档、生产安全文档和相关流程图。外部 Obsidian 笔记作为需求/RCA 参考，不替代仓库事实表。
