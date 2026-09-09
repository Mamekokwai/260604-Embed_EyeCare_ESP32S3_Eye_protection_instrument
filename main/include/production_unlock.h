#pragma once

#include <stdbool.h>

#include "sdkconfig.h"

#ifndef CONFIG_EYECARE_PRODUCTION_LOCK
#define CONFIG_EYECARE_PRODUCTION_LOCK 0
#endif

/* 生产构建由 Kconfig 开启；开发构建不进入授权状态机。 */
#define EYECARE_ENABLE_ENCRYPTION CONFIG_EYECARE_PRODUCTION_LOCK

/**
 * 等待本机通过 USB Serial-JTAG 完成单机授权。
 * 开发构建立即返回 true；生产构建只有在 MAC、NVS 序列号、challenge
 * 和 P-256 签名全部匹配后才写入 EYECARE_UNLOCKED eFuse 并返回 true。
 */
bool production_unlock_ensure(void);
