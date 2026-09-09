# ESP32-S3 生产安全与单机授权

当前仓库的开发配置默认关闭生产锁定、Secure Boot 和 Flash Encryption。本文描述量产配置启用后的流程；普通开发板不得烧录生产配置，也不得写入 eFuse。

## 安全边界

- Secure Boot V2 验证 bootloader 和应用签名。
- Release Flash Encryption 保护固件和标记为 encrypted 的分区。
- `EYECARE_UNLOCKED` 是一次性 eFuse 标记，只有设备绑定授权验签通过后才写入。
- 授权不再使用 TF 卡 `/eyecare.unlock` 共享文件，改为 USB Serial-JTAG 逐台授权。
- 授权私钥只保存在离线签发机；固件只内嵌 P-256 公钥。

## 设备身份与授权协议

设备身份由两部分组成：出厂 eFuse MAC 和首次启动时生成并写入 NVS 的 16 字节随机序列号。设备在锁定启动阶段仅通过 USB Serial-JTAG 接收以下命令：

```text
READ_ID\r\n
ID MAC=AA:BB:CC:DD:EE:FF SERIAL=<32 hex>\r\n

CHALLENGE\r\n
CHALLENGE <32 hex nonce>\r\n

ACTIVATE <token hex>\r\n
ATC_OK | INVALID_FOR_DEVICE | ERR_LOCKED | ERR_EFUSE\r\n
```

授权帧二进制格式为：

```text
magic(8) = EYEUNLK2 | version(1) | key_id(1) | reserved(2) |
nonce(16) | factory_mac(6) | nvs_serial(16) |
signature_length(1) | ECDSA-P256-DER-signature(<=72)
```

签名覆盖前 50 字节完整载荷。设备同时比对当前 challenge、自己的 MAC 和 NVS 序列号，然后用内嵌公钥验证签名；任一字段不匹配都不会写 eFuse。challenge 使用后立即失效，防止重放。已授权设备上电直接进入主程序，重复授权不会再次写 eFuse。

## 失败锁定

失败计数保存在 NVS `eyecare` 命名空间的 `unlock_failures` 键中。生产模式默认 10 次失败后永久返回 `ERR_LOCKED`；开发测试可启用 `EYECARE_UNLOCK_TEST_MODE`，使用默认 100 次阈值。计数达到阈值后，即使随后收到正确授权也拒绝，需返厂处理。NVS 擦除会影响该计数，因此量产必须同时启用 NVS 加密并限制物理调试入口。

## 离线签发工具

生成密钥并导出公钥：

```bash
python tools/security/unlock_token.py generate-key --private-key <offline>/eyecare_unlock_ecdsa_p256
python tools/security/unlock_token.py export-public \
  --private-key <offline>/eyecare_unlock_ecdsa_p256 \
  --output main/include/unlock_public_key.h
```

从设备读取 `READ_ID` 和 `CHALLENGE` 后，为单台设备签发：

```bash
python tools/security/unlock_token.py issue \
  --private-key <offline>/eyecare_unlock_ecdsa_p256 \
  --output device.token \
  --mac AA:BB:CC:DD:EE:FF \
  --serial <READ_ID 返回的 32 位十六进制> \
  --nonce <CHALLENGE 返回的 32 位十六进制>
```

工具会打印完整的 `ACTIVATE <hex>` 行，将该行通过 USB Serial-JTAG 发送即可。token 不能复制到另一台设备使用。

## 量产构建与顺序

生产 `sdkconfig.production` 至少应确认：

```text
CONFIG_EYECARE_PRODUCTION_LOCK=y
CONFIG_SECURE_BOOT_V2_ENABLED=y
CONFIG_SECURE_SIGNED_APPS_RSA_SCHEME=y
CONFIG_SECURE_FLASH_ENCRYPTION_AES256=y
CONFIG_SECURE_FLASH_ENCRYPTION_MODE_RELEASE=y
CONFIG_SECURE_ENABLE_SECURE_ROM_DL_MODE=y
CONFIG_NVS_ENCRYPTION=y
```

使用独立构建目录并运行只读预检：

```bash
idf.py -B build-production \
  -D SDKCONFIG=sdkconfig.production \
  -D 'SDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.production.defaults' build
python tools/security/production_preflight.py \
  --build-dir build-production --sdkconfig sdkconfig.production
```

每台设备的推荐顺序：记录 MAC → 烧录签名生产镜像和媒体 → 首次启动读取 `READ_ID` → 发送 `CHALLENGE` → 离线签发设备绑定 token → 发送 `ACTIVATE` → 读取并记录 `ATC_OK` 与 eFuse 回读 → 断电重启验证无需 TF 卡即可运行。任何 eFuse 写入前必须确认工单、芯片 MAC、序列号和签发记录完全一致。

## 必测安全场景

1. A 设备 token 发送给 B 设备，B 返回 `INVALID_FOR_DEVICE`。
2. 修改 token 任意字节，验签失败并递增计数。
3. 重放已使用的 challenge/token，必须失败。
4. 连续达到生产阈值后返回 `ERR_LOCKED`，正确 token 也不能解锁。
5. 正确 token 只写入一次 eFuse；断电重启后直接运行。
6. 未授权设备不能进入媒体、UART1 或 CA51 业务主循环。
7. Secure Boot、Flash Encryption、ROM 下载保护和 NVS 加密均处于量产状态。

## 运维风险

Secure Boot、Release Flash Encryption、eFuse 和失败锁定均不可逆或难以恢复。必须保管至少两份离线密钥备份、签名公钥指纹、每台设备授权审计记录和生产镜像哈希；不要把私钥、token、`sdkconfig.production` 或未加密生产媒体提交到 Git。
