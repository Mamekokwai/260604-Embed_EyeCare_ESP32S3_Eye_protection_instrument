# 2026-11 视频偶发卡顿：ESP32-S3 只读审计

日期：2026-11-22

范围：ESP32-S3 `f9d3086`（`dev2`）源码、提交历史和版本未核实的历史日志片段。
限制：本记录只做审计，不修改源码、不调参数、不烧录。

## 1. 版本边界

对比基线为 11 月前的 `74a8b78`，重点检查：

| 提交 | 日期 | 与视频/音频相关的变化 | 判断 |
| --- | --- | --- | --- |
| `6534deb` | 10-06 | 音频 I2S DMA 从 6×240 改为 12×480；音频任务改为高频循环；视频读取缓冲 32 KiB 改为 16 KiB | 11 月前已有，不能归因于 11 月改动，但会影响并发余量 |
| `12eb7c0` | 10-10 | LCD 条带高度 160 改为 80 | 11 月前已有；每帧由 2 条增加到 4 条，事务/回调次数翻倍 |
| `1327b5b` | 11-11 | 新增 `APLAYONCE`、vocal 单播、一次性播放结束排空 | 11 月新增音频生命周期分支；音视频并发基础此前已存在 |
| `500be5b` | 11-12 | 修改 `video_player.c` 的 `next_ts` 推进时机 | 11 月新增；host gate 对照未发现稳定回归 |
| `f42e287` / `f9d3086` | 11-22 | 临时背光恢复；`BLOFF/BLON` 恢复门控 | 不触碰视频解码、SD 读取和音频供数路径 |

## 2. 当前实现证据

### 2.1 帧时钟：已完成源码对照回归

`main/video_player.c:649-698` 在 `500be5b` 前后发生了以下变化：

- 基线版本：帧到期后，先执行 `next_ts += SecPerFrame`，再检查 LCD 是否可提交。
- 当前版本：只有通过 `lcd_is_ready()` 和 `spilcd_wait_te()` 后才推进 `next_ts`；首帧从 gate 完成时重新开始计时。

当前 AVI 为 30 fps 时，`SecPerFrame=33333 us`。除首帧外，当前代码在接受一帧后始终执行绝对的 `next_ts += SecPerFrame`，不存在每次 gate 等待都累加到时间戳的逻辑。`500be5b` 修复的是“LCD gate 未就绪时仍提前推进 deadline”；gate 本身若晚完成仍会推迟本帧提交，但这属于单帧延迟，不能据此断言存在累积漂移。

补充：仓库内没有 `LCD_TE_ENABLE` 定义，`components/BSP/SPILCD/spilcd.c:550` 的 `spilcd_wait_te()` 在当前构建中会走立即返回分支。因此 `500be5b` 的主要运行时差异只剩“等待上一笔 LCD DMA 完成后再推进时间戳”，不是 TE GPIO 等待。

源码片段 host 对照见 `doc/issue/VIDEO_STUTTER_202611_FRAME_GATE.json`，脚本为 `tools/diagnostics/test_video_frame_gate.py`：

- 正常 100 帧：旧、新策略均 PASS；
- 首帧 gate 忙后 1 ms 可用：旧策略在 33333 us 接受，新策略在 1000 us 接受；
- 第二帧 gate 忙、35000 us 可用：旧策略在 66666 us 接受，新策略在 35000 us 接受；
- 2 s 晚帧追赶策略：旧、新一致。

因此 `500be5b` 是一次 gate 重试语义修正，host 测试没有发现稳定播放回归；它应保留为待实机确认项，不应仅凭提交日期优先回退。

### 2.2 解码与音频任务竞争

- JPEG 解码任务：`main/video_player.c:47`，CPU1、优先级 4。
- 音频任务：`main/audio_player.c:29-30,413-441`，CPU1、优先级 5。
- 音频每次成功供数后调用 `taskYIELD()`，只有 `PLAYER_BUSY` 才延时 1 ms。
- MP3 解码后通过 `main/audio_player.c:328` 调用 `audio_write_pcm()`；底层 `i2s_channel_write(..., 1000)` 位于 `managed_components/espressif__esp_codec_dev/platform/audio_codec_data_i2s.c:525`。

这是一条真实的并发竞争路径：音频计算就绪时可抢占 JPEG 解码，I2S 写入等待时则会让出 CPU。它不是 11 月首次引入（优先级和独立音频任务来自更早提交）；`1327b5b` 改变了单播及结束分支，但没有证据证明客户的持续并发负载因此增加。

### 2.3 SD 读取与 LCD 条带

- `main/video_player.c:40` 的 AVI 读取缓冲为 16 KiB（`6534deb` 已改小）。
- `main/video_player.c:43-45` 条带高度 80、条带缓冲数量 1；320×320 一帧需要 4 次 LCD DMA 提交。
- `main/video_player.c:633-645` 在主任务中同步提交下一帧解码并执行 `read_one_chunk()`；`read_one_chunk()` 会跳过 AVI 音频/未知块并按需触发 FatFS 读取。
- `components/BSP/SPILCD/spilcd.c:134-136` 使用 30 MHz I80、事务队列深度 2；视频自身只有 1 个条带缓冲，不能形成真正的双条带提交流水线。

该组合会增加每帧提交次数和 SD/FatFS 调度敏感性，但全部早于 11 月已存在，属于背景风险，不是本次版本新增。

### 2.4 主循环和 SD 探卡

- `main/main.c:417-440` 主任务每 1 ms 取 tick；播放视频时每 tick 调 `display_tick()`。
- `main/main.c:169-199` 每约 2 s 调一次 `sd_card_probe()`，失败时会停止视频/音频并卸载 SD。
- `components/BSP/SPILCD/spilcd.c:547-575` 的 `spilcd_wait_te()` 在当前工程未定义 `LCD_TE_ENABLE`，预处理后走 `#if !LCD_TE_ENABLE` 的立即返回分支；因此当前版本没有实际 TE 忙等阻塞。

SD 探卡与视频读取共享同一 SD 总线，理论上可能产生短暂事务竞争；该逻辑自 9 月已存在。即使 `sd_card_probe()` 成功，CMD13 事务本身也会占用主任务/总线时间；因此“没有 SD unresponsive 日志”不能排除成功路径阻塞，必须测量 probe 耗时后才能排除。

### 2.5 日志输出和监控锁竞争

- `VID profile` 的 12 行统计来自 `081fe6e`，不是 11 月新增；默认日志级别为 INFO，串口为 115200 bit/s。
- ESP-IDF 当前日志链路为同步 `vprintf`/UART 输出。每 100 帧输出一次，30 fps 约每 3.33 s 发生一次；输出本身会占用主任务时间，且 profile 计算在视频 tick 完成帧的路径上。
- `main/main.c:169-203` 每约 2 s 记录一次堆/播放状态。`audio_player_is_active()` 在 `main/audio_player.c:583-588` 先获取音频递归互斥锁；音频服务在持锁期间执行 `audio_player_tick_locked()`，该函数可进入 `audio_write_pcm()` 和底层 `i2s_channel_write(..., 1000)`。因此监控日志路径存在“主任务等待音频锁”的可能。

这两项都是历史代码（profile 来自 `081fe6e`，2 s 监控/锁路径早于 11 月），但会形成与客户“无按键、周期性偶发卡顿”相符的时间相关性。当前 profile 的平均值不能排除某一帧刚好被日志或锁等待拖长。

## 3. 已有日志的判读

本节中的运行日志是本对话中用户此前贴出的串口片段（时间戳约 11:35、11:42），未带 ELF SHA256/完整固件版本，不能证明就是客户所说的上周 `f9d3086` 实机版本。当前源码、媒体和日志预算的可复核证据另存于 `doc/issue/VIDEO_STUTTER_202611_EVIDENCE.json`，采集时间为 2026-11-22 16:00（源码 HEAD=`f9d3086`）；该 JSON 明确标注“无设备性能测量”。

该对话历史片段的示例窗口（未核实固件版本，不作为本次客户运行证据）：

```text
window/avg : 3332 ms, 30.0 fps, 33.32 ms/frame
wait decode: 47 ms (1.4%)
SD read    : 895 ms (26.9%)
rate ctrl  : 747 ms (22.4%)
LCD refresh: 1296 ms (38.9%)
JPEG decode: 1783 ms (53.5%)
Waiting JPEG decode: started=302 completed=301
audio_player: supply: max_gap=32 ms gaps>40ms=0
```

结论：

1. 100 帧平均吞吐为 30 fps，JPEG 平均解码、SD 读取的墙钟时间均低于 33.33 ms，未显示持续性欠速。
2. `started=completed+1` 是双缓冲流水线存在一个在途解码任务的正常形态；单凭这一条不能认定丢帧。
3. `max_gap=32 ms, gaps>40ms=0` 说明该窗口音频供数没有出现已定义的 40 ms 断供阈值；音频不是该窗口的直接证据，但仍可能通过 CPU1 优先级造成解码时刻抖动。
4. 当前 profile 只记录 100 帧平均值，没有记录每帧提交时间、最大帧间隔、实际丢帧数，所以无法用它排除偶发抖动。

### 3.1 同步串口 profile 的预算风险

`VIDEO_STUTTER_202611_EVIDENCE.json` 对 12 条 profile 格式化日志做了模型计算：样例合计 738 bytes，115200 8N1 的理想线路时间约 64.06 ms；即使扣除 128-byte FIFO，仍约 52.95 ms，超过 33.33 ms 的单帧预算。该数值是模型，不是硬件测量，但足以说明 profile 输出存在把主任务/日志链路拖过一帧预算的可能。profile 代码来自历史提交 `081fe6e`，所以不能把它归咎于 11 月新增；应在复现时关闭 profile 或提高调试链路带宽做 A/B。

## 4. 优先级排序

1. **最高优先级：同步 profile 日志造成的主任务阻塞风险。** 12 行/738 bytes 在 115200 下模型线路时间约 64.06 ms，超过 33.33 ms 帧预算；先关闭 profile 或提高日志链路做 A/B。
2. **中高优先级候选：CPU1 音频优先级 5 抢占 JPEG 解码优先级 4。** 调度基础早于11月；需在“仅视频 / 视频+BGM / 视频+vocal”三组条件下对比解码等待和帧间隔。
3. **中优先级候选：同步日志/2 s 监控锁。** `audio_player_is_active()` 可能等待音频服务持有的互斥锁；需统计锁等待时长，不能仅凭平均 FPS 判断。
4. **中优先级候选：80 行单条带 + 单缓冲。** 每帧 4 次 DMA 提交，容易放大 LCD/SD 调度抖动；这是 10 月改动，不能单独解释 11 月时间关联。
5. **待测候选：2 s SD `CMD13` 探测。** 成功和失败路径都可能占用时间，必须实测耗时后再判断。

## 5. 最小复现与下一步验证

后续可先做三组运行。每组建议120～180秒；若要观察10分钟，需在受控台架上确保CA51不会自动接BGM、在5分钟时切入脉冲或关机，不能直接把正常训练下的10分钟记录当作单一视频稳态：

1. 先发送 `ASTOP`，再执行 `VID 1`，避免已有 CA51 自动 BGM 干预“仅视频”基线；
2. `ASTOP` → `VID 1` → `APLAY music/1.mp3`；
3. `ASTOP` → `VID 1` → `APLAYONCE music/vocal/弱视.mp3`，再按训练流程接 BGM。

注意：`VID` 本身不会停止音频；因此不能只发 `VID 1` 就声称是“无音频”测试。上述路径必须使用设备上实际存在的完整 vocal 路径。

每组保存：

- `VID profile` 的 100 帧窗口；
- `Waiting JPEG decode` 次数；
- `audio_player: supply` 的 `max_gap` 和 `gaps>40ms`；
- 是否在约 2 s 边界出现 `sd_card_probe`/`SD card removed`。

若要确认帧时钟，下一轮仅增加诊断计数（不调参数）：记录每帧第一条 LCD strip 的 `esp_timer_get_time()` 间隔、最大值、超过 40 ms 的次数。只有拿到该数据后，才决定回退/重写 `next_ts`。

## 6. 当前审计结论

当前没有证据证明 f42e287/f9d3086 的背光恢复改动造成卡顿；它们未修改视频时钟、SD 读取、LCD DMA 或音频供数。`500be5b` 的源码反例回归已通过，证明其修复 gate deadline 提前推进且稳态/超期追赶策略一致，不应仅因时间关联回退。源码层面最确定的风险是同步 profile 日志可能超过单帧预算；其次是 CPU1 音频高优先级与 JPEG 解码竞争。最终因果仍需在客户实际固件上按 `ASTOP` 基线、BGM、完整 vocal 路径分别做帧间隔 A/B，并单独测量成功 `CMD13` 耗时。
