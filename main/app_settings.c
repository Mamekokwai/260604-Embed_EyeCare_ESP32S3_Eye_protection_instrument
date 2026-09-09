#include "app_settings.h"
#include "sdkconfig.h"

#include <stdbool.h>

#include "esp_system.h"
#include "esp_random.h"
#include "nvs.h"
#include "nvs_flash.h"

#define SETTINGS_NAMESPACE "eyecare"
#define SETTINGS_KEY_VOLUME "volume"
#define SETTINGS_KEY_BACKLIGHT "backlight"
#define SETTINGS_KEY_DEVICE_SERIAL "device_serial"
#define SETTINGS_KEY_UNLOCK_FAILURES "unlock_failures"
#define DEFAULT_VOLUME 70
#define DEFAULT_BACKLIGHT 100

static bool valid_volume(uint8_t value)
{
    return value >= APP_VOLUME_MIN && value <= APP_VOLUME_MAX;
}

static bool valid_backlight(uint8_t value)
{
    return value >= APP_BACKLIGHT_MIN && value <= APP_BACKLIGHT_MAX;
}

esp_err_t app_settings_init(void)
{
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES ||
        ret == ESP_ERR_NVS_NEW_VERSION_FOUND)
    {
#if CONFIG_EYECARE_PRODUCTION_LOCK
        /* 生产模式不能自动擦除 NVS：设备序列号和失败锁定计数必须保留。 */
        return ret;
#else
        ret = nvs_flash_erase();
        if (ret == ESP_OK)
            ret = nvs_flash_init();
#endif
    }
    return ret;
}

void app_settings_load(app_settings_t *settings)
{
    if (!settings)
        return;

    settings->volume = DEFAULT_VOLUME;
    settings->backlight = DEFAULT_BACKLIGHT;

    nvs_handle_t handle;
    if (nvs_open(SETTINGS_NAMESPACE, NVS_READONLY, &handle) != ESP_OK)
        return;

    uint8_t value;
    if (nvs_get_u8(handle, SETTINGS_KEY_VOLUME, &value) == ESP_OK &&
        valid_volume(value))
        settings->volume = value;
    if (nvs_get_u8(handle, SETTINGS_KEY_BACKLIGHT, &value) == ESP_OK &&
        valid_backlight(value))
        settings->backlight = value;
    nvs_close(handle);
}

static esp_err_t save_percent(const char *key, uint8_t value, uint8_t min_value)
{
    if (value < min_value || value > 100)
        return ESP_ERR_INVALID_ARG;

    nvs_handle_t handle;
    esp_err_t ret = nvs_open(SETTINGS_NAMESPACE, NVS_READWRITE, &handle);
    if (ret != ESP_OK)
        return ret;

    ret = nvs_set_u8(handle, key, value);
    if (ret == ESP_OK)
        ret = nvs_commit(handle);
    nvs_close(handle);
    return ret;
}

esp_err_t app_settings_save_volume(uint8_t volume)
{
    return save_percent(SETTINGS_KEY_VOLUME, volume, APP_VOLUME_MIN);
}

esp_err_t app_settings_save_backlight(uint8_t backlight)
{
    return save_percent(SETTINGS_KEY_BACKLIGHT, backlight, APP_BACKLIGHT_MIN);
}

esp_err_t app_settings_get_device_serial(uint8_t serial[APP_DEVICE_SERIAL_SIZE])
{
    if (!serial)
        return ESP_ERR_INVALID_ARG;

    nvs_handle_t handle;
    esp_err_t ret = nvs_open(SETTINGS_NAMESPACE, NVS_READWRITE, &handle);
    if (ret != ESP_OK)
        return ret;

    size_t length = APP_DEVICE_SERIAL_SIZE;
    ret = nvs_get_blob(handle, SETTINGS_KEY_DEVICE_SERIAL, serial, &length);
    if (ret == ESP_ERR_NVS_NOT_FOUND)
    {
        esp_fill_random(serial, APP_DEVICE_SERIAL_SIZE);
        ret = nvs_set_blob(handle, SETTINGS_KEY_DEVICE_SERIAL,
                           serial, APP_DEVICE_SERIAL_SIZE);
        if (ret == ESP_OK)
            ret = nvs_commit(handle);
    }
    else if (ret == ESP_OK && length != APP_DEVICE_SERIAL_SIZE)
    {
        ret = ESP_ERR_INVALID_SIZE;
    }
    nvs_close(handle);
    return ret;
}

esp_err_t app_settings_get_unlock_failures(uint32_t *failures)
{
    if (!failures)
        return ESP_ERR_INVALID_ARG;
    *failures = 0;

    nvs_handle_t handle;
    esp_err_t ret = nvs_open(SETTINGS_NAMESPACE, NVS_READONLY, &handle);
    if (ret != ESP_OK)
        return ret == ESP_ERR_NVS_NOT_FOUND ? ESP_OK : ret;
    ret = nvs_get_u32(handle, SETTINGS_KEY_UNLOCK_FAILURES, failures);
    if (ret == ESP_ERR_NVS_NOT_FOUND)
        ret = ESP_OK;
    nvs_close(handle);
    return ret;
}

esp_err_t app_settings_set_unlock_failures(uint32_t failures)
{
    nvs_handle_t handle;
    esp_err_t ret = nvs_open(SETTINGS_NAMESPACE, NVS_READWRITE, &handle);
    if (ret != ESP_OK)
        return ret;
    ret = nvs_set_u32(handle, SETTINGS_KEY_UNLOCK_FAILURES, failures);
    if (ret == ESP_OK)
        ret = nvs_commit(handle);
    nvs_close(handle);
    return ret;
}
