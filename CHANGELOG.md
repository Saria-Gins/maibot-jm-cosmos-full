# Changelog

## 1.1.10

- 上传的 PDF 文件名改为纯 JM 编号（`<编号>.pdf`），不再暴露本子原名
- 移除不再使用的 `_safe_name` / `_ILLEGAL`

## 1.1.9

- 修复 1.1.8 中 `plugin.py` 被误写为空文件导致插件无法加载的问题
- 许可证统一为 AGPL-3.0-or-later（原项目即 AGPL-3.0）
- 移除 `@Tool` 上无效的 `activation_type` 参数与 `ActivationType` 导入
- Command 返回值第三项改为明确权重 `1`
- 新增用户 ID 兜底（`message.message_info.user_info.user_id`）
- `plugin.enabled` 现在真实生效

## 1.1.7

- 超过体积上限的 PDF 也照常发送，发送后（不区分成败）一律删除本地文件
- 移除 `pdf_max_mb` 配置项
- `on_config_update` 的 scope 判断兼容 `self`
- `i18n` 去掉不存在的 `locales_path`
- `pymupdf` 依赖加上上界 `<2`，`jmcomic` 加上 `<3`
- 下载版移出插件中心仓库，独立维护

## 1.1.6 及更早

见插件中心仓库 `Saria-Gins/maibot-jm-cosmos` 的历史提交。