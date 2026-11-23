# 文档与固件一致性记录

本仓库以 `doc/CURRENT_IMPLEMENTATION.md` 为事实基准，完整协议见 `doc/UART_COMMANDS.md`，移植说明见 `doc/PORTING.md`。生产安全文档仅供 `product` 分支维护，`main` 不编译或执行生产授权链路。

## 本次安全架构更新

- 生产授权、Secure Boot、Flash Encryption、NVS 加密和 eFuse 操作只在 `product` 分支维护；`main` 保持无授权、无加密的开发实现。

## 维护规则

修改安全协议、引脚、外设链路或启动行为时，同时更新事实表、UART 文档、生产安全文档和相关流程图。外部 Obsidian 笔记作为需求/RCA 参考，不替代仓库事实表。
