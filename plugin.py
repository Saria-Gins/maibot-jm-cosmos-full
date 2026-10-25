"""JM-Cosmos for MaiBot.

由 AstrBot 插件 astrbot_plugin_jm_cosmos 移植：搜索、查看详情、下载单行本并打包成 PDF 发送。
"""

from __future__ import annotations

import asyncio
import io
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import jmcomic
import pymupdf
from PIL import Image

from maibot_sdk import Command, Field, MaiBotPlugin, PluginConfigBase, Tool

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}


def _is_within(child: Path, parent: Path) -> bool:
    """判断路径是否位于父目录内，避免拼接值跳出下载根目录。"""

    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _collect_images(root: Path) -> List[Path]:
    """按路径顺序收集下载目录里的图片，保证页码顺序。"""

    files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES]
    return sorted(files, key=lambda p: (p.parent.as_posix(), p.name))


class PluginSection(PluginConfigBase):
    """插件基础配置。"""

    __ui_label__ = "插件"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用插件")
    config_version: str = Field(default="1.0.0", description="配置版本")


class DownloadSection(PluginConfigBase):
    """下载与打包配置。"""

    __ui_label__ = "下载"
    __ui_order__ = 1

    base_dir: str = Field(default="", description="下载根目录；留空则使用插件数据目录下的 downloads")
    max_pages: int = Field(default=300, ge=1, le=2000, description="单个本子最多允许多少页")
    timeout_seconds: int = Field(default=600, ge=30, le=3600, description="单次下载超时时间（秒）")
    keep_images: bool = Field(default=False, description="打包完成后是否保留原始图片")
    delete_pdf_after_send: bool = Field(default=True, description="发送成功后自动删除 PDF 文件")


class AccessSection(PluginConfigBase):
    """权限配置。"""

    __ui_label__ = "权限"
    __ui_order__ = 2

    allowed_users: List[str] = Field(default_factory=list, description="允许使用的 QQ 号；留空表示所有人可用")
    allowed_groups: List[str] = Field(default_factory=list, description="允许使用的群号；allow_groups 开启后生效，留空表示所有群可用")
    allow_groups: bool = Field(default=False, description="是否允许在群聊中使用；默认关闭，成人向标题不会直接进群")


class JMCosmosConfig(PluginConfigBase):
    """JM 插件总配置。"""

    plugin: PluginSection = Field(default_factory=PluginSection)
    download: DownloadSection = Field(default_factory=DownloadSection)
    access: AccessSection = Field(default_factory=AccessSection)


class JMCosmosPlugin(MaiBotPlugin):
    """JM 漫画下载插件。"""

    config_model = JMCosmosConfig

    def __init__(self) -> None:
        super().__init__()
        self._locks: Dict[str, asyncio.Lock] = {}

    async def on_load(self) -> None:
        root = self._base_dir()
        root.mkdir(parents=True, exist_ok=True)
        self.ctx.logger.info(f"[JM] 插件已加载，下载目录：{root}")

    async def on_unload(self) -> None:
        self._locks.clear()

    async def on_config_update(self, scope: str, config_data: Dict[str, Any], version: str) -> None:
        """配置热更新：插件自身配置变更时重建下载目录。"""

        del config_data
        if scope not in ("plugin", "self"):
            return
        root = self._base_dir()
        root.mkdir(parents=True, exist_ok=True)
        self.ctx.logger.info(f"[JM] 配置已更新（版本 {version}），下载目录：{root}")

    # ------------------------------------------------------------------ 工具方法

    def _base_dir(self) -> Path:
        """解析下载根目录；相对路径按 MaiBot 实例根目录展开。"""

        raw = str(self.config.download.base_dir or "").strip()
        if not raw:
            return Path(self.ctx.paths.data_dir) / "downloads"
        path = Path(raw)
        return path if path.is_absolute() else Path.cwd() / path

    @staticmethod
    def _resolve_user_id(kwargs: Dict[str, Any]) -> str:
        """从 kwargs 或 message 结构解析调用者 QQ。"""

        value = str(kwargs.get("user_id") or "").strip()
        if value.isdigit():
            return value
        message = kwargs.get("message")
        if isinstance(message, dict):
            info = message.get("message_info") or {}
            if isinstance(info, dict):
                user_info = info.get("user_info") or {}
                if isinstance(user_info, dict):
                    value = str(user_info.get("user_id") or "").strip()
                    if value.isdigit():
                        return value
        return ""

    @staticmethod
    def _resolve_group_id(stream_id: str, kwargs: Dict[str, Any]) -> str:
        """尽量解析当前会话的群号；私聊返回空串。"""

        for key in ("group_id", "group", "gid"):
            value = str(kwargs.get(key) or "").strip()
            if value.isdigit():
                return value
        message = kwargs.get("message")
        if isinstance(message, dict):
            info = message.get("message_info") or {}
            if isinstance(info, dict):
                group_info = info.get("group_info") or {}
                if isinstance(group_info, dict):
                    value = str(group_info.get("group_id") or "").strip()
                    if value.isdigit():
                        return value
                value = str(info.get("group_id") or "").strip()
                if value.isdigit():
                    return value
        matched = re.search(r"(?:group|群)[:：_\-]?(\d{5,})", str(stream_id or ""))
        return matched.group(1) if matched else ""

    def _check_permission(self, user_id: str, group_id: str = "") -> bool:
        """判断调用者是否有权使用：先看用户白名单，再看群白名单。"""

        allowed_users = [str(x).strip() for x in self.config.access.allowed_users if str(x).strip()]
        if allowed_users and str(user_id).strip() not in allowed_users:
            return False
        group = str(group_id or "").strip()
        if group and not self.config.access.allow_groups:
            return False
        allowed_groups = [str(x).strip() for x in self.config.access.allowed_groups if str(x).strip()]
        if allowed_groups and group and group not in allowed_groups:
            return False
        return True

    def _get_lock(self, stream_id: str) -> asyncio.Lock:
        """同一会话串行下载，避免重复任务互相覆盖目录。"""

        lock = self._locks.get(stream_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[stream_id] = lock
        return lock

    @staticmethod
    def _build_option(base_dir: Path) -> "jmcomic.JmOption":
        """构造下载选项：目录固定到本次任务目录。"""

        option = jmcomic.JmOption.default()
        option.dir_rule.base_dir = str(base_dir)
        return option

    def _fetch_detail(self, album_id: str) -> Dict[str, Any]:
        """同步读取专辑详情。"""

        option = jmcomic.JmOption.default()
        client = option.build_jm_client()
        detail = client.get_album_detail(album_id)
        return {
            "id": str(getattr(detail, "album_id", album_id)),
            "name": str(getattr(detail, "name", "") or ""),
            "page_count": int(getattr(detail, "page_count", 0) or 0),
            "authors": list(getattr(detail, "authors", []) or []),
            "tags": list(getattr(detail, "tags", []) or []),
        }

    def _search(self, keyword: str, limit: int = 8) -> List[Dict[str, str]]:
        """同步搜索专辑。"""

        option = jmcomic.JmOption.default()
        client = option.build_jm_client()
        page = client.search_site(keyword, 1)
        results: List[Dict[str, str]] = []
        for item in list(getattr(page, "content", []) or [])[:limit]:
            album_id, info = item
            results.append({
                "id": str(album_id),
                "name": str((info or {}).get("name") or ""),
                "author": str((info or {}).get("author") or ""),
            })
        return results

    @staticmethod
    def _page_items(page: Any, limit: int = 8) -> List[Dict[str, str]]:
        """把 JmSearchPage 的 content 统一成 {id, name, author} 列表。"""

        items: List[Dict[str, str]] = []
        for item in list(getattr(page, "content", []) or [])[:limit]:
            album_id, info = item
            items.append({
                "id": str(album_id),
                "name": str((info or {}).get("name") or ""),
                "author": str((info or {}).get("author") or ""),
            })
        return items

    def _ranking(self, kind: str, page: int, limit: int = 8) -> List[Dict[str, str]]:
        """获取周榜/月榜。"""

        option = jmcomic.JmOption.default()
        client = option.build_jm_client()
        fetch = client.week_ranking if kind == "week" else client.month_ranking
        return self._page_items(fetch(page), limit)

    def _recommend(self, order_by: str, time_range: str, page: int, limit: int = 8) -> List[Dict[str, str]]:
        """按排序与时间范围获取推荐列表。"""

        option = jmcomic.JmOption.default()
        client = option.build_jm_client()
        result = client.categories_filter(page, time_range, "0", order_by)
        return self._page_items(result, limit)

    @staticmethod
    def _format_album_list(title: str, items: List[Dict[str, str]]) -> str:
        """把条目列表格式化成可读文本。"""

        if not items:
            return f"{title}：没有取到内容，稍后再试。"
        lines = [f"{idx}. [{item['id']}] {item['name'][:60]}" for idx, item in enumerate(items, 1)]
        return f"{title}\n" + "\n".join(lines) + "\n\n用 /jm 编号 下载，/jmi 编号 看详情。"

    def _download_album(self, album_id: str, target: Path) -> None:
        """同步下载整本专辑到目标目录。"""

        option = self._build_option(target)
        option.download_album(album_id)

    @staticmethod
    def _images_to_pdf(images: List[Path], pdf_path: Path) -> Tuple[int, int]:
        """把图片按顺序写进 PDF，返回 (成功页数, 失败张数)。

        MuPDF 不认 webp，先用 Pillow 统一转成 JPEG 再插入。
        """

        out = pymupdf.open()
        written = 0
        failed = 0
        try:
            for image_path in images:
                try:
                    with Image.open(image_path) as im:
                        rgb = im.convert("RGB")
                        width, height = rgb.size
                        buf = io.BytesIO()
                        rgb.save(buf, format="JPEG", quality=90)
                    jpeg_bytes = buf.getvalue()
                    page = out.new_page(width=width, height=height)
                    page.insert_image(pymupdf.Rect(0, 0, width, height), stream=jpeg_bytes)
                    written += 1
                except Exception:  # noqa: BLE001 - 单张坏图不中断整本
                    failed += 1
                    continue
            if written == 0:
                raise RuntimeError("所有图片都无法转换，未生成 PDF")
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            out.save(pdf_path, deflate=True)
        finally:
            out.close()
        return written, failed

    async def _send_pdf(self, pdf_path: Path, stream_id: str, album_name: str) -> bool:
        """优先以文件形式发送 PDF，失败则回退为文本路径。"""

        size = pdf_path.stat().st_size
        payload = {
            "name": pdf_path.name,
            "size": size,
            "url": pdf_path.resolve().as_uri(),
            "mime_type": "application/pdf",
        }
        try:
            result = await self.ctx.send.custom("file", payload, stream_id)
            if result:
                return True
        except Exception as exc:  # noqa: BLE001 - 平台可能不支持文件段
            self.ctx.logger.warning(f"[JM] 文件发送失败：{exc}")
        await self.ctx.send.text(
            f"《{album_name}》打包完成，但平台不接受文件消息。",
            stream_id,
        )
        return False

    # ------------------------------------------------------------------ 命令

    @Command("jm", description="下载 JM 单行本并打包 PDF：/jm 123456", pattern=r"^/jm\s+(?P<album_id>\d+)")
    async def cmd_download(self, stream_id: str = "", user_id: str = "", matched_groups: Optional[Dict[str, str]] = None, **kwargs: Any) -> Tuple[bool, str, bool]:
        if not self.config.plugin.enabled:
            return True, "", 1
        album_id = str((matched_groups or {}).get("album_id") or "").strip()
        if not album_id:
            await self.ctx.send.text("用法：/jm 本子编号", stream_id)
            return True, "", 1
        if not self._check_permission(user_id, self._resolve_group_id(stream_id, kwargs)):
            await self.ctx.send.text("你没有使用 JM 下载的权限。", stream_id)
            return True, "", 1

        lock = self._get_lock(stream_id)
        if lock.locked():
            await self.ctx.send.text("本会话已有下载任务在进行，请等它完成。", stream_id)
            return True, "", 1

        async with lock:
            try:
                detail = await asyncio.to_thread(self._fetch_detail, album_id)
            except Exception as exc:  # noqa: BLE001
                await self.ctx.send.text(f"读取本子信息失败：{exc}", stream_id)
                return True, "", 1

            pages = detail["page_count"]
            if pages > self.config.download.max_pages:
                await self.ctx.send.text(
                    f"《{detail['name']}》共 {pages} 页，超过上限 {self.config.download.max_pages} 页，已拒绝。",
                    stream_id,
                )
                return True, "", 1

            name = detail["name"] or album_id
            base_dir = self._base_dir()
            target = base_dir / album_id
            if not _is_within(target, base_dir):
                await self.ctx.send.text("下载目录校验失败，已中止。", stream_id)
                return True, "", 1
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            target.mkdir(parents=True, exist_ok=True)

            await self.ctx.send.text(f"开始下载《{name}》（{pages} 页），请稍候…", stream_id)
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(self._download_album, album_id, target),
                    timeout=self.config.download.timeout_seconds,
                )
            except asyncio.TimeoutError:
                await self.ctx.send.text("下载超时，已中止。", stream_id)
                return True, "", 1
            except Exception as exc:  # noqa: BLE001
                await self.ctx.send.text(f"下载失败：{exc}", stream_id)
                return True, "", 1

            images = _collect_images(target)
            if not images:
                await self.ctx.send.text("下载完成但没找到图片，可能是站点异常。", stream_id)
                return True, "", 1
            await self.ctx.send.text(f"图片下载完成（{len(images)} 张），正在打包 PDF…", stream_id)

            # 上传文件名只用编号，避免把本子原名暴露在会话里
            pdf_path = self._base_dir() / "pdf" / f"{album_id}.pdf"
            try:
                written, failed = await asyncio.to_thread(self._images_to_pdf, images, pdf_path)
            except Exception as exc:  # noqa: BLE001
                await self.ctx.send.text(f"PDF 生成失败：{exc}", stream_id)
                return True, "", 1
            if not self.config.download.keep_images:
                shutil.rmtree(target, ignore_errors=True)

            if written == 0:
                await self.ctx.send.text("PDF 生成失败：没有可用图片。", stream_id)
                return True, "", 1

            size_mb = pdf_path.stat().st_size / 1048576
            failed_hint = f"，跳过 {failed} 张坏图" if failed else ""
            await self.ctx.send.text(f"打包完成：{written} 页，{size_mb:.1f} MB{failed_hint}", stream_id)
            sent = await self._send_pdf(pdf_path, stream_id, name)
            if not sent:
                await self.ctx.send.text(
                    f"文件发送失败（{size_mb:.1f} MB），可能是平台体积上限；按配置不在本地保留。",
                    stream_id,
                )
            if self.config.download.delete_pdf_after_send:
                try:
                    pdf_path.unlink(missing_ok=True)
                    self.ctx.logger.info(f"[JM] 已删除本地 PDF（发送结果={sent}）：{pdf_path}")
                except Exception as exc:  # noqa: BLE001
                    self.ctx.logger.warning(f"[JM] 删除 PDF 失败：{exc}")
            return True, "", 1

    @Command("jmi", description="查看 JM 本子详情：/jmi 123456", pattern=r"^/jmi\s+(?P<album_id>\d+)")
    async def cmd_info(self, stream_id: str = "", user_id: str = "", matched_groups: Optional[Dict[str, str]] = None, **kwargs: Any) -> Tuple[bool, str, bool]:
        if not self.config.plugin.enabled:
            return True, "", 1
        user_id = user_id or self._resolve_user_id(kwargs)
        group_id = self._resolve_group_id(stream_id, kwargs)
        if not self._check_permission(user_id, group_id):
            hint = "群聊默认未开放，请让管理员在配置中开启 allow_groups" if group_id else "你没有使用 JM 的权限。"
            await self.ctx.send.text(hint, stream_id)
            return True, "", 1
        album_id = str((matched_groups or {}).get("album_id") or "").strip()
        try:
            detail = await asyncio.to_thread(self._fetch_detail, album_id)
        except Exception as exc:  # noqa: BLE001
            await self.ctx.send.text(f"读取失败：{exc}", stream_id)
            return True, "", 1
        tags = "、".join(str(t) for t in detail["tags"][:8]) or "无"
        authors = "、".join(str(a) for a in detail["authors"][:4]) or "未知"
        await self.ctx.send.text(
            f"《{detail['name']}》\n编号：{detail['id']}\n页数：{detail['page_count']}\n作者：{authors}\n标签：{tags}",
            stream_id,
        )
        return True, "", 1

    @Command("jms", description="搜索 JM 本子：/jms 关键词", pattern=r"^/jms\s+(?P<keyword>.+)$")
    async def cmd_search(self, stream_id: str = "", user_id: str = "", matched_groups: Optional[Dict[str, str]] = None, **kwargs: Any) -> Tuple[bool, str, bool]:
        if not self.config.plugin.enabled:
            return True, "", 1
        user_id = user_id or self._resolve_user_id(kwargs)
        group_id = self._resolve_group_id(stream_id, kwargs)
        if not self._check_permission(user_id, group_id):
            hint = "群聊默认未开放，请让管理员在配置中开启 allow_groups" if group_id else "你没有使用 JM 的权限。"
            await self.ctx.send.text(hint, stream_id)
            return True, "", 1
        keyword = str((matched_groups or {}).get("keyword") or "").strip()
        if not keyword:
            await self.ctx.send.text("用法：/jms 关键词", stream_id)
            return True, "", 1
        try:
            results = await asyncio.to_thread(self._search, keyword, 8)
        except Exception as exc:  # noqa: BLE001
            await self.ctx.send.text(f"搜索失败：{exc}", stream_id)
            return True, "", 1
        if not results:
            await self.ctx.send.text("没有搜到结果。", stream_id)
            return True, "", 1
        lines = [f"{idx}. [{item['id']}] {item['name'][:60]}" for idx, item in enumerate(results, 1)]
        await self.ctx.send.text("搜索结果：\n" + "\n".join(lines) + "\n\n用 /jm 编号 下载。", stream_id)
        return True, "", 1

    @Command("jmrank", description="查看 JM 周榜/月榜：/jmrank week 1", pattern=r"^/jmrank(?:\s+(?P<args>.*))?$")
    async def cmd_rank(self, stream_id: str = "", user_id: str = "", matched_groups: Optional[Dict[str, str]] = None, **kwargs: Any) -> Tuple[bool, str, bool]:
        if not self.config.plugin.enabled:
            return True, "", 1
        user_id = user_id or self._resolve_user_id(kwargs)
        group_id = self._resolve_group_id(stream_id, kwargs)
        if not self._check_permission(user_id, group_id):
            hint = "群聊默认未开放，请让管理员在配置中开启 allow_groups" if group_id else "你没有使用 JM 的权限。"
            await self.ctx.send.text(hint, stream_id)
            return True, "", 1
        raw = str((matched_groups or {}).get("args") or "").strip()
        kind = "week"
        page = 1
        for token in raw.split():
            lower = token.lower()
            if lower in ("week", "w", "周", "周榜"):
                kind = "week"
            elif lower in ("month", "m", "月", "月榜"):
                kind = "month"
            elif lower.isdigit():
                page = max(1, int(lower))
            else:
                await self.ctx.send.text(f"未知参数：{token}\n用法：/jmrank [week|month] [页码]", stream_id)
                return True, "", 1
        try:
            items = await asyncio.to_thread(self._ranking, kind, page, 8)
        except Exception as exc:  # noqa: BLE001
            await self.ctx.send.text(f"获取榜单失败：{exc}", stream_id)
            return True, "", 1
        title = f"JM {'周榜' if kind == 'week' else '月榜'} 第 {page} 页"
        await self.ctx.send.text(self._format_album_list(title, items), stream_id)
        return True, "", 1

    @Command("jmrec", description="推荐本子：/jmrec [排序] [时间] [页码]", pattern=r"^/jmrec(?:\s+(?P<args>.*))?$")
    async def cmd_recommend(self, stream_id: str = "", user_id: str = "", matched_groups: Optional[Dict[str, str]] = None, **kwargs: Any) -> Tuple[bool, str, bool]:
        if not self.config.plugin.enabled:
            return True, "", 1
        user_id = user_id or self._resolve_user_id(kwargs)
        group_id = self._resolve_group_id(stream_id, kwargs)
        if not self._check_permission(user_id, group_id):
            hint = "群聊默认未开放，请让管理员在配置中开启 allow_groups" if group_id else "你没有使用 JM 的权限。"
            await self.ctx.send.text(hint, stream_id)
            return True, "", 1
        raw = str((matched_groups or {}).get("args") or "").strip()
        order_map = {
            "latest": "mr", "mr": "mr",
            "view": "mv", "mv": "mv",
            "picture": "mp", "mp": "mp",
            "like": "tf", "tf": "tf",
            "score": "tr", "tr": "tr",
        }
        time_map = {
            "all": "a", "a": "a",
            "today": "t", "t": "t",
            "week": "w", "w": "w",
            "month": "m", "m": "m",
        }
        order_by = "mr"
        time_range = "a"
        page = 1
        for token in raw.split():
            lower = token.lower()
            if lower in order_map:
                order_by = order_map[lower]
            elif lower in time_map:
                time_range = time_map[lower]
            elif lower.isdigit():
                page = max(1, int(lower))
            else:
                await self.ctx.send.text(
                    f"未知参数：{token}\n用法：/jmrec [latest|view|picture|like|score] [all|today|week|month] [页码]",
                    stream_id,
                )
                return True, "", 1
        try:
            items = await asyncio.to_thread(self._recommend, order_by, time_range, page, 8)
        except Exception as exc:  # noqa: BLE001
            await self.ctx.send.text(f"获取推荐失败：{exc}", stream_id)
            return True, "", 1
        order_name = {"mr": "最新", "mv": "最多观看", "mp": "最多图片", "tf": "最多喜欢", "tr": "最高评分"}.get(order_by, order_by)
        time_name = {"a": "全部时间", "t": "今天", "w": "本周", "m": "本月"}.get(time_range, time_range)
        title = f"JM 推荐 · {time_name} · {order_name} 第 {page} 页"
        await self.ctx.send.text(self._format_album_list(title, items), stream_id)
        return True, "", 1

    @Command("jmhelp", description="JM 插件帮助", pattern=r"^/jmhelp\s*$")
    async def cmd_help(self, stream_id: str = "", **kwargs: Any) -> Tuple[bool, str, bool]:
        await self.ctx.send.text(
            "JM 漫画插件\n/jm 编号　下载整本并打包 PDF\n/jmi 编号　查看详情\n/jms 关键词　搜索\n/jmrank [week|month] [页]　榜单\n/jmrec [排序] [时间] [页]　推荐\n/jmhelp　本帮助",
            stream_id,
        )
        return True, "", 1

    # ------------------------------------------------------------------ LLM 工具

    @Tool(
        "jm_search_albums",
        description="搜索 JM 漫画本子，返回编号与标题，便于之后下载。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
            },
            "required": ["query"],
        },
    )
    async def tool_search(self, query: str = "", **kwargs: Any) -> Dict[str, Any]:
        keyword = str(query or "").strip()
        if not keyword:
            return {"success": False, "content": "缺少关键词"}
        try:
            results = await asyncio.to_thread(self._search, keyword, 6)
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "content": f"搜索失败：{exc}"}
        if not results:
            return {"success": False, "content": "没有搜到结果"}
        text = "\n".join(f"[{item['id']}] {item['name']}" for item in results)
        return {"success": True, "content": text}

def create_plugin() -> JMCosmosPlugin:
    """宿主加载入口：返回插件实例。"""

    return JMCosmosPlugin()
