# Changelog

## 1.1.7

- 超过体积上限的 PDF 也照常发送，发送后（不区分成败）一律删除本地文件
- 移除 `pdf_max_mb` 配置项
- `on_config_update` 的 scope 判断兼容 `self`
- `i18n` 去掉不存在的 `locales_path`
- `pymupdf` 依赖加上上界 `<2`，`jmcomic` 加上 `<3`
- 下载版移出插件中心仓库，独立维护

## 1.1.6 及更早

见插件中心仓库 `Saria-Gins/maibot-jm-cosmos` 的历史提交。