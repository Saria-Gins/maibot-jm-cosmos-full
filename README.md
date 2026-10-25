# JM-Cosmos Full for MaiBot

JM 漫画**下载**插件：搜索、榜单、推荐、下载单行本并打包 PDF 发送。

> 本插件移植自 [GEMILUXVII/astrbot_plugin_jm_cosmos](https://github.com/GEMILUXVII/astrbot_plugin_jm_cosmos) 的查询部分，感谢原作者的开源工作。
>
> **本版本不在插件中心分发**，仅供自建环境使用。插件中心的收录版本是只读查询版：[Saria-Gins/maibot-jm-cosmos](https://github.com/Saria-Gins/maibot-jm-cosmos-full)。

## 功能

| 命令 | 说明 | 示例 |
| --- | --- | --- |
| `/jmi 编号` | 查看标题、页数、作者、标签 | `/jmi 1480638` |
| `/jms 关键词` | 搜索，返回编号与标题 | `/jms 关键词` |
| `/jmrank [week\|month] [页码]` | 周榜 / 月榜 | `/jmrank month 2` |
| `/jmrec [排序] [时间] [页码]` | 按排序与时间取推荐 | `/jmrec view week` |
| `/jmhelp` | 帮助 | `/jmhelp` |

`/jmrec` 参数（均可省略）：

- 排序：`latest` / `view` / `picture` / `like` / `score`（也接受 `mr` `mv` `mp` `tf` `tr`）
- 时间：`all` / `today` / `week` / `month`（也接受 `a` `t` `w` `m`）

另外提供 LLM 工具 `jm_search_albums`，只返回编号与标题。

## 安装

1. 把本仓库克隆或解压到 `MaiBot/plugins/maibot-jm-cosmos/`
2. 安装依赖：

```bash
pip install "jmcomic>=2.6.10,<3" "pymupdf>=1.23,<2"
```

3. 重启 MaiBot。首次启动会按配置模型生成 `config.toml`。

## 配置

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `query.timeout_seconds` | 45 | 单次查询超时 |
| `query.max_results` | 8 | 每个列表最多返回条数 |
| `access.allowed_users` | 空 | 用户白名单；留空表示所有人 |
| `access.allowed_groups` | 空 | 群白名单；留空表示所有群 |
| `access.allow_groups` | true | 是否允许群聊使用 |

权限规则：用户白名单命中 → 群白名单命中 → 群聊开关。三层都通过才放行。

## ⚠️ 依赖冲突说明

`jmcomic` 依赖一个**顶层包 `common`**，而 MaiBot 自己也有 `src/common`。插件运行时 `src` 在 `sys.path` 里，`import common` 会解析到 MaiBot 的实现，报错：

```
ImportError: cannot import name 'time_stamp' from 'common'
```

`jmcomic` 在多处函数体内做懒加载，插件层无法绕过。可选方案：

1. **推荐**：给 jmcomic 单独 venv，插件通过子进程调用
2. 修改 site-packages（本仓库 `scripts/fix_common_conflict.py`）：**属于破坏性操作**，脚本默认只预演，`--apply` 才执行，且会先检查是否还有其它包引用 `common`、并自动备份

升级或重装 jmcomic 后需要重新处理。

## 实现说明

- 所有网络访问只经 `jmcomic` 请求 JM 站点，不涉及聊天记录、用户 ID、群号或插件配置
- 阻塞调用放在线程池里并带超时，不阻塞事件循环
- 输出只包含标题、编号、页数、作者、标签等元数据

## 已知限制

- 目标站点连接不稳，`jmcomic` 会自动换域名重试
- 周榜接口目前经常返回空列表，属站点侧情况
- 站点内容以成人向为主，是否查询、在什么范围展示由部署者自行负责

## 致谢

- 原作者：[**GEMILUXVII**](https://github.com/GEMILUXVII)
- 原项目：[astrbot_plugin_jm_cosmos](https://github.com/GEMILUXVII/astrbot_plugin_jm_cosmos)
- 本移植版：[Saria-Gins/maibot-jm-cosmos](https://github.com/Saria-Gins/maibot-jm-cosmos-full)

## 许可证

GPL-3.0-or-later，与原项目一致。详见 [LICENSE](LICENSE)。