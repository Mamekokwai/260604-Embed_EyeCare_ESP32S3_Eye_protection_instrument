# ESP32-S3 眼保仪固件

## 分支使用规则

- `main`：日常开发、逻辑修改、烧录测试和硬件验证分支；不包含生产授权、Secure Boot 或 Flash Encryption 实现。
- `product`：生产版本分支。平时禁止修改；只有明确要求制作或更新生产固件时才修改，并使用生产加密构建流程。
- `main-screen-v2`：新屏幕 V2 的开发测试分支，基于 `main`，只维护屏幕驱动适配，使用规则与 `main` 相同。

生产固件的 Secure Boot、Release Flash Encryption、NVS 加密和二次授权配置只从 `product` 分支制作。`main` 仅用于开发测试；修改生产分支前应先确认需求、密钥和 eFuse 操作计划。

目标模组：`ESP32-S3-WROOM-1U-N16R8`（ESP32-S3，16 MiB Flash、8 MiB Octal PSRAM，外置天线版本）。

项目文档已统一收录在 [`doc/`](doc/)：

- [项目说明与构建指南](doc/README.md)
- [当前实现基准](doc/CURRENT_IMPLEMENTATION.md)
- [UART 指令手册](doc/UART_COMMANDS.md)
- [代码移植指南](doc/PORTING.md)
- [生产安全与解锁流程](doc/SECURITY_PROVISIONING.md)
- [量产前 TODO](doc/TODO.md)
- [文档一致性清单](doc/DOCUMENTATION_ALIGNMENT.md)

源码、工具和硬件资料仍按工程目录组织；`AGENTS.md` 是唯一的仓库 AI/开发规则文件，保留在根目录供开发工具读取。
