#!/usr/bin/env python3
"""Stash overrides published by the daily sync.

Add a row to ``SOURCES`` to publish another override. ``kind`` selects the
converter:

- ``surge-sgmodule``: a Surge ``.sgmodule`` file
- ``qx-rewrite``: a Quantumult X rewrite. ``name`` overrides the userscript
  title. Author, homepage, and the source URL in the file header come from
  the rewrite itself. The table still links ``author`` to ``homepage``.

Surge ``#!arguments`` defaults are read from each module. They are not copied
into this table, so an upstream default change flows through on the next run.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote


# Raw files on the default branch. Install links and script failover point here.
RAW_PREFIX = "https://raw.githubusercontent.com/wsc82/Clash/main/"

# Other validation failures must repeat this many daily runs before failover.
# HTTP 404 fails over on the first run, once a mirrored copy exists.
FAILURE_THRESHOLD = 3

SOURCES: list[dict[str, object]] = [
    {
        "output": "Stash/YouTube.stoverride",
        "kind": "surge-sgmodule",
        "source_url": "https://raw.githubusercontent.com/Maasea/sgmodule/master/YouTube.Enhance.sgmodule",
        "author": "Maasea",
        "homepage": "https://github.com/Maasea/sgmodule",
        "license_spdx": "Apache-2.0",
        "license_url": "https://raw.githubusercontent.com/Maasea/sgmodule/master/LICENSE",
        "license_file": "Stash/scripts/Maasea/LICENSE",
        # Protobuf descriptors are compiled into the scripts. The only extra
        # runtime URL is a live worker; it is not a file we can mirror.
        "runtime_notes": (
            "protobuf 描述已经编进脚本，没有单独的描述文件或参数文件。"
            "youtube.request.js 在 initplayback 命中缓存 key 时会请求 "
            "https://init-stream.maasea.workers.dev/ 。这是动态接口，地址写死在脚本里；"
            "镜像必须与上游字节一致，所以不能把这个地址改成本仓库。"
            "上游脚本文件被删时，去广告脚本仍从本仓库加载；该接口若同时不可用，只有这段 initplayback 重定向会受影响。"
        ),
        # QUIC is blocked so YouTube falls back to TCP and MITM can see it.
        # These are not in the upstream module.
        "extra_rules_comment": "拦掉 YouTube 的 QUIC（UDP），让它回落到 TCP，这样 MitM 才能生效",
        "extra_rules": [
            "AND,((DOMAIN-SUFFIX,googlevideo.com),(PROTOCOL,QUIC)),REJECT",
            "AND,((DOMAIN-SUFFIX,youtubei.googleapis.com),(PROTOCOL,QUIC)),REJECT",
        ],
    },
    {
        "output": "Stash/StartUpAds.stoverride",
        "kind": "qx-rewrite",
        "source_url": "https://ddgksf2013.top/rewrite/StartUpAds.conf",
        "name": "墨鱼去开屏",
        "author": "ddgksf2013",
        "homepage": "https://github.com/ddgksf2013/Rewrite",
        "attribution": "配置原文要求「如需引用请注明出处」。",
        "runtime_notes": (
            "其中 baidumap.js 来自 app2smile/rules（MIT），许可证全文见 "
            "scripts/app2smile/LICENSE.md。一条 script-analyze-echo-response（12306）"
            "转成了 request 脚本。以分号开头的禁用行被跳过，记在覆写末尾。"
        ),
        "companion_files": [
            {
                "url": "https://raw.githubusercontent.com/app2smile/rules/master/LICENSE.md",
                "file": "Stash/scripts/app2smile/LICENSE.md",
                "kind": "license",
                "spdx": "MIT",
            }
        ],
    },
    {
        "output": "Stash/CheLaiLeAds.stoverride",
        "kind": "qx-rewrite",
        "source_url": "https://raw.githubusercontent.com/ddgksf2013/Rewrite/refs/heads/master/AdBlock/CheLaiLeAds.conf",
        "name": "车来了去广告",
        "author": "ddgksf2013",
        "homepage": "https://github.com/ddgksf2013/Rewrite",
        "attribution": "配置原文要求「如需引用请注明出处」。",
    },
    {
        "output": "Stash/AmapAds.stoverride",
        "kind": "qx-rewrite",
        "source_url": "https://raw.githubusercontent.com/ddgksf2013/Rewrite/master/AdBlock/AmapAds.conf",
        "name": "高德地图去广告",
        "author": "ddgksf2013",
        "homepage": "https://github.com/ddgksf2013/Rewrite",
        "attribution": "配置原文要求「如需引用请注明出处」。",
        "runtime_notes": (
            "脚本匹配了 amdc.m.taobao.com，但上游 hostname 列表里没有这个域名。"
            "Stash 不会对它做 MitM，除非在别处加上该主机。"
        ),
    },
    {
        "output": "Stash/BiliBiliAdsLite.stoverride",
        "kind": "qx-rewrite",
        "source_url": "https://ddgksf2013.top/rewrite/BiliBiliAdsLite.conf",
        "name": "哔哩哔哩去广告(残缺版)",
        "author": "ddgksf2013",
        "homepage": "https://github.com/ddgksf2013/Rewrite",
        "attribution": "配置原文要求「如需引用请注明出处」。",
    },
    {
        "output": "Stash/MiGuVideoAds.stoverride",
        "kind": "qx-rewrite",
        "source_url": "https://ddgksf2013.top/rewrite/MiGuVideoAds.conf",
        "name": "咪咕视频去广告",
        "author": "ddgksf2013",
        "homepage": "https://github.com/ddgksf2013/Rewrite",
        "attribution": "配置原文要求「如需引用请注明出处」。",
    },
]


def raw_url(output: str) -> str:
    """Public raw URL of an override on main."""
    return RAW_PREFIX + quote(output, safe="/")


def install_url(output: str) -> str:
    """One-click Stash install link for the raw override."""
    return "stash://install-override?url=" + quote(raw_url(output), safe="")


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def render_readme(sources: list[dict[str, object]]) -> str:
    """Chinese index of every source. The table is the whole catalog."""
    lines = [
        "# Stash 覆写",
        "",
        "这些文件由上游配置自动转换成 Stash 覆写。每天 00:45（北京时间）同步一次。请不要手工修改本目录里的文件，下次同步会覆盖修改。",
        "",
        "覆写里的脚本地址默认仍指向原作者。同步会把每个脚本按字节下载到 `scripts/<作者>/`。上游提供许可证时，许可证放在脚本旁边（Maasea 为 Apache-2.0，见 `scripts/Maasea/LICENSE`；StartUpAds 引用的 baidumap.js 来自 app2smile，MIT，见 `scripts/app2smile/LICENSE.md`）。上游没有许可证或 NOTICE 时不另造一份，只保留原作者署名和来源。脚本文件头保持原样，不做修改。",
        "",
        "上游脚本地址连续 3 次校验失败，或返回 404 时，覆写会改用本仓库 raw 镜像；上游恢复后自动改回。校验不通过（404、下载错误、HTML、空文件、过小、或 JavaScript 语法错误）时保留上一份可用副本，不会把脚本或覆写清空。每个上游单独处理，一个失败不影响其他来源。",
        "",
        "这份镜像只供个人备份，不能拿去当成自己的作品再分发。著作权归原作者所有。",
        "",
        "| 覆写 | 原作者 | 上游 | 安装 |",
        "| --- | --- | --- | --- |",
    ]
    authors: list[str] = []
    for source in sources:
        output = str(source["output"])
        filename = Path(output).name
        author = str(source["author"])
        homepage = str(source["homepage"])
        source_url = str(source["source_url"])
        if author not in authors:
            authors.append(author)
        lines.append(
            "| "
            + " | ".join(
                [
                    f"[{_cell(filename)}]({filename})",
                    f"[{_cell(author)}]({homepage})",
                    _cell(source_url),
                    f"[一键安装]({install_url(output)})",
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 感谢",
            "",
            f"感谢原作者 {'、'.join(authors)} 等。规则与脚本的著作权归原作者所有。这里只做 Stash 格式转换，并按上游原文保留一份备份，不把原作者的作品改头换面。",
            "",
            "## 许可证",
            "",
        ]
    )
    for source in sources:
        filename = Path(str(source["output"])).name
        author = str(source["author"])
        homepage = str(source["homepage"])
        note = source.get("runtime_notes")
        note_text = f" {note}" if isinstance(note, str) and note else ""
        spdx = source.get("license_spdx")
        license_file = source.get("license_file")
        if isinstance(spdx, str) and isinstance(license_file, str):
            relative = license_file.split("/", 1)[1] if license_file.startswith("Stash/") else license_file
            notice = source.get("notice_file")
            if notice:
                notice_text = f"上游 NOTICE 见 `{notice}`。"
            else:
                notice_text = "上游没有 NOTICE 文件。"
            lines.append(
                f"- `{filename}` 转换自 [{author}]({homepage}) 的配置，许可证为 {spdx}。{notice_text}全文见 [{relative}]({relative})。脚本镜像在 `scripts/{author}/`，与上游字节相同。{note_text}"
            )
        else:
            attribution = source.get("attribution")
            attribution_text = attribution if isinstance(attribution, str) and attribution else "引用时请注明出处。"
            lines.append(
                f"- `{filename}` 转换自 [{author}]({homepage}) 的配置。上游仓库没有 SPDX 许可证文件。{attribution_text}脚本镜像在 `scripts/{author}/`，与上游字节相同。{note_text}"
            )
    lines.extend(
        [
            "",
            "有 SPDX 许可证的上游，该许可证覆盖对应的转换文件和从上游镜像过来的脚本。没有许可证文件的上游只按原作者要求署名。这不改变本仓库里其他文件的许可。",
            "",
        ]
    )
    return "\n".join(lines)
