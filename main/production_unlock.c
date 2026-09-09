#include "production_unlock.h"

#include "sdkconfig.h"

#if EYECARE_ENABLE_ENCRYPTION

#if !CONFIG_SECURE_BOOT_V2_ENABLED || !CONFIG_SECURE_FLASH_ENC_ENABLED || \
    !CONFIG_SECURE_FLASH_ENCRYPTION_MODE_RELEASE
#error "Production unlock requires Secure Boot V2 and Release Flash Encryption"
#endif

#include <stdio.h>
#include <string.h>
#include <limits.h>

#include "driver/usb_serial_jtag.h"
#include "esp_efuse.h"
#include "esp_efuse_custom_table.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_random.h"
#include "esp_flash_encrypt.h"
#include "esp_secure_boot.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "mbedtls/pk.h"
#include "mbedtls/sha256.h"
#include "app_settings.h"
#include "unlock_public_key.h"

#define TAG "prod_unlock"
#define UNLOCK_MAGIC "EYEUNLK2"
#define UNLOCK_VERSION 2U
#define UNLOCK_KEY_ID 1U
#define UNLOCK_NONCE_SIZE 16U
#define UNLOCK_SERIAL_SIZE APP_DEVICE_SERIAL_SIZE
#define UNLOCK_PAYLOAD_SIZE (8U + 1U + 1U + 2U + UNLOCK_NONCE_SIZE + 6U + UNLOCK_SERIAL_SIZE)
#define UNLOCK_SIGNATURE_MAX 72U
#define UNLOCK_TOKEN_MAX (UNLOCK_PAYLOAD_SIZE + 1U + UNLOCK_SIGNATURE_MAX)
#define UNLOCK_LINE_SIZE 640U

#ifndef CONFIG_EYECARE_UNLOCK_FAILURE_THRESHOLD_TEST
#define CONFIG_EYECARE_UNLOCK_FAILURE_THRESHOLD_TEST 100
#endif
#ifndef CONFIG_EYECARE_UNLOCK_FAILURE_THRESHOLD_PRODUCTION
#define CONFIG_EYECARE_UNLOCK_FAILURE_THRESHOLD_PRODUCTION 10
#endif
#ifndef CONFIG_EYECARE_UNLOCK_TEST_MODE
#define CONFIG_EYECARE_UNLOCK_TEST_MODE 0
#endif

static uint8_t s_serial[UNLOCK_SERIAL_SIZE];
static uint8_t s_mac[6];
static uint8_t s_nonce[UNLOCK_NONCE_SIZE];
static bool s_nonce_valid;
static bool s_usb_ready;
static char s_line[UNLOCK_LINE_SIZE];
static size_t s_line_length;

static uint32_t failure_threshold(void)
{
#if CONFIG_EYECARE_UNLOCK_TEST_MODE
    return CONFIG_EYECARE_UNLOCK_FAILURE_THRESHOLD_TEST;
#else
    return CONFIG_EYECARE_UNLOCK_FAILURE_THRESHOLD_PRODUCTION;
#endif
}

static void usb_write_line(const char *line)
{
    if (!line || !s_usb_ready)
        return;
    char response[UNLOCK_LINE_SIZE + 3U];
    int length = snprintf(response, sizeof(response), "%s\r\n", line);
    if (length > 0)
        (void)usb_serial_jtag_write_bytes(response, (uint32_t)length,
                                          pdMS_TO_TICKS(100));
}

static void bytes_to_hex(const uint8_t *bytes, size_t length, char *out,
                         size_t out_size)
{
    static const char hex[] = "0123456789ABCDEF";
    if (!out || out_size < length * 2U + 1U)
        return;
    for (size_t i = 0; i < length; ++i)
    {
        out[i * 2U] = hex[bytes[i] >> 4];
        out[i * 2U + 1U] = hex[bytes[i] & 0x0fU];
    }
    out[length * 2U] = '\0';
}

static int hex_value(char ch)
{
    if (ch >= '0' && ch <= '9') return ch - '0';
    if (ch >= 'a' && ch <= 'f') return ch - 'a' + 10;
    if (ch >= 'A' && ch <= 'F') return ch - 'A' + 10;
    return -1;
}

static esp_err_t hex_decode(const char *text, uint8_t *out, size_t out_size,
                            size_t *decoded)
{
    if (!text || !out || !decoded)
        return ESP_ERR_INVALID_ARG;
    size_t length = strlen(text);
    if ((length & 1U) != 0 || length / 2U > out_size)
        return ESP_ERR_INVALID_SIZE;
    for (size_t i = 0; i < length / 2U; ++i)
    {
        int high = hex_value(text[i * 2U]);
        int low = hex_value(text[i * 2U + 1U]);
        if (high < 0 || low < 0)
            return ESP_ERR_INVALID_ARG;
        out[i] = (uint8_t)((high << 4) | low);
    }
    *decoded = length / 2U;
    return ESP_OK;
}

static void send_device_id(void)
{
    char serial_hex[UNLOCK_SERIAL_SIZE * 2U + 1U];
    bytes_to_hex(s_serial, sizeof(s_serial), serial_hex, sizeof(serial_hex));
    char response[128];
    snprintf(response, sizeof(response),
             "ID MAC=%02X:%02X:%02X:%02X:%02X:%02X SERIAL=%s",
             s_mac[0], s_mac[1], s_mac[2], s_mac[3], s_mac[4], s_mac[5],
             serial_hex);
    usb_write_line(response);
}

static bool is_locked(uint32_t failures)
{
    return failures >= failure_threshold();
}

static void record_failure(uint32_t *failures)
{
    if (!failures || *failures >= UINT32_MAX)
        return;
    ++(*failures);
    esp_err_t ret = app_settings_set_unlock_failures(*failures);
    if (ret != ESP_OK)
        ESP_LOGE(TAG, "cannot persist unlock failure count: %s",
                 esp_err_to_name(ret));
}

static bool verify_token(const uint8_t *token, size_t token_size)
{
    if (!token || token_size < UNLOCK_PAYLOAD_SIZE + 2U)
        return false;
    if (memcmp(token, UNLOCK_MAGIC, 8U) != 0 ||
        token[8] != UNLOCK_VERSION || token[9] != UNLOCK_KEY_ID ||
        token[10] != 0 || token[11] != 0)
        return false;

    const uint8_t *nonce = token + 12U;
    const uint8_t *mac = nonce + UNLOCK_NONCE_SIZE;
    const uint8_t *serial = mac + 6U;
    if (!s_nonce_valid || memcmp(nonce, s_nonce, UNLOCK_NONCE_SIZE) != 0 ||
        memcmp(mac, s_mac, sizeof(s_mac)) != 0 ||
        memcmp(serial, s_serial, sizeof(s_serial)) != 0)
        return false;

    uint8_t signature_size = token[UNLOCK_PAYLOAD_SIZE];
    if (signature_size == 0 || signature_size > UNLOCK_SIGNATURE_MAX ||
        token_size != UNLOCK_PAYLOAD_SIZE + 1U + signature_size)
        return false;

    mbedtls_pk_context key;
    mbedtls_pk_init(&key);
    int ret = mbedtls_pk_parse_public_key(
        &key, EYECARE_UNLOCK_PUBLIC_KEY_DER,
        EYECARE_UNLOCK_PUBLIC_KEY_DER_LEN);
    if (ret == 0)
    {
        uint8_t digest[32];
        ret = mbedtls_sha256(token, UNLOCK_PAYLOAD_SIZE, digest, 0);
        if (ret == 0)
            ret = mbedtls_pk_verify(&key, MBEDTLS_MD_SHA256, digest,
                                    sizeof(digest),
                                    token + UNLOCK_PAYLOAD_SIZE + 1U,
                                    signature_size);
    }
    mbedtls_pk_free(&key);
    return ret == 0;
}

static bool burn_unlock_efuse(void)
{
    esp_err_t ret = esp_efuse_write_field_bit(ESP_EFUSE_USER_DATA_EYECARE_UNLOCKED);
    if (ret != ESP_OK)
    {
        ESP_LOGE(TAG, "failed to write unlock eFuse: %s", esp_err_to_name(ret));
        return false;
    }
    ret = esp_efuse_batch_write_commit();
    if (ret != ESP_OK)
    {
        ESP_LOGE(TAG, "failed to commit unlock eFuse: %s", esp_err_to_name(ret));
        return false;
    }
    return esp_efuse_read_field_bit(ESP_EFUSE_USER_DATA_EYECARE_UNLOCKED);
}

static void handle_unlock_line(const char *line, uint32_t *failures,
                               bool *activated)
{
    if (!line || !failures || !activated)
        return;
    if (is_locked(*failures))
    {
        usb_write_line("ERR_LOCKED");
        return;
    }
    if (strcmp(line, "READ_ID") == 0)
    {
        send_device_id();
        return;
    }
    if (strcmp(line, "CHALLENGE") == 0)
    {
        esp_fill_random(s_nonce, sizeof(s_nonce));
        s_nonce_valid = true;
        char nonce_hex[UNLOCK_NONCE_SIZE * 2U + 1U];
        bytes_to_hex(s_nonce, sizeof(s_nonce), nonce_hex, sizeof(nonce_hex));
        char response[64];
        snprintf(response, sizeof(response), "CHALLENGE %s", nonce_hex);
        usb_write_line(response);
        return;
    }
    if (strncmp(line, "ACTIVATE ", 9U) != 0)
    {
        usb_write_line("ERR_UNKNOWN");
        return;
    }

    uint8_t token[UNLOCK_TOKEN_MAX];
    size_t token_size = 0;
    if (hex_decode(line + 9U, token, sizeof(token), &token_size) != ESP_OK ||
        !verify_token(token, token_size))
    {
        record_failure(failures);
        usb_write_line(is_locked(*failures) ? "ERR_LOCKED" : "INVALID_FOR_DEVICE");
        return;
    }

    if (!burn_unlock_efuse())
    {
        usb_write_line("ERR_EFUSE");
        return;
    }
    (void)app_settings_set_unlock_failures(0);
    s_nonce_valid = false;
    *activated = true;
    usb_write_line("ATC_OK");
}

bool production_unlock_ensure(void)
{
    if (!esp_secure_boot_enabled() || !esp_flash_encryption_enabled())
    {
        ESP_LOGE(TAG, "Secure Boot/Flash Encryption eFuses are not active");
        return false;
    }
    if (esp_efuse_read_field_bit(ESP_EFUSE_USER_DATA_EYECARE_UNLOCKED))
        return true;

    if (!s_usb_ready)
    {
        if (usb_serial_jtag_is_driver_installed())
            s_usb_ready = true;
        else
        {
            usb_serial_jtag_driver_config_t config =
                USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
            esp_err_t ret = usb_serial_jtag_driver_install(&config);
            if (ret != ESP_OK)
            {
                ESP_LOGE(TAG, "USB Serial-JTAG unlock channel unavailable: %s",
                         esp_err_to_name(ret));
                return false;
            }
            s_usb_ready = true;
        }
    }

    if (esp_efuse_mac_get_default(s_mac) != ESP_OK ||
        app_settings_get_device_serial(s_serial) != ESP_OK)
    {
        ESP_LOGE(TAG, "cannot read device identity from eFuse/NVS");
        return false;
    }

    uint32_t failures = 0;
    esp_err_t ret = app_settings_get_unlock_failures(&failures);
    if (ret != ESP_OK)
    {
        ESP_LOGE(TAG, "cannot read unlock failure count: %s",
                 esp_err_to_name(ret));
        return false;
    }
    usb_write_line(is_locked(failures) ? "ERR_LOCKED" : "UNLOCK_READY");

    bool activated = false;
    while (!activated)
    {
        uint8_t byte;
        while (usb_serial_jtag_read_bytes(&byte, 1, 0) > 0)
        {
            if (byte == '\r' || byte == '\n')
            {
                if (s_line_length > 0)
                {
                    s_line[s_line_length] = '\0';
                    handle_unlock_line(s_line, &failures, &activated);
                    s_line_length = 0;
                }
            }
            else if (s_line_length + 1U < sizeof(s_line))
                s_line[s_line_length++] = (char)byte;
            else
            {
                s_line_length = 0;
                record_failure(&failures);
                usb_write_line(is_locked(failures) ? "ERR_LOCKED" : "INVALID_FOR_DEVICE");
            }
        }
        if (!activated)
            vTaskDelay(pdMS_TO_TICKS(CONFIG_EYECARE_UNLOCK_RETRY_MS));
    }
    return true;
}

#else

bool production_unlock_ensure(void)
{
    return true;
}

#endif
