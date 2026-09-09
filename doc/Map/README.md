# 文档地图

本目录保存媒体链路和生产启动流程图。图源应与 `doc/CURRENT_IMPLEMENTATION.md`、`doc/SECURITY_PROVISIONING.md` 同步。

- `media_pipeline_archify.md`：视频、音频、图片和启动授权链路（Mermaid）。
- `sd-playback-chain.html` / `sd-playback-chain.dataflow.json`：SD 播放数据流。
- `sd-resource-rule.html` / `sd-resource-rule.architecture.json`：SD 资源规则。
- `production-unlock.html` / `production-unlock.lifecycle.json`：生产安全启动生命周期；授权阶段为 USB Serial-JTAG 的 READ_ID、CHALLENGE、ACTIVATE 单机绑定，不再使用 TF 卡共享令牌。
