# Stash 覆写

这些文件由上游配置自动转换成 Stash 覆写。每天 00:45（北京时间）同步一次。请不要手工修改本目录里的文件，下次同步会覆盖修改。

覆写里的脚本地址默认仍指向原作者。同步会把每个脚本按字节下载到 `scripts/<作者>/`。上游提供许可证时，许可证放在脚本旁边（Maasea 为 Apache-2.0，见 `scripts/Maasea/LICENSE`；StartUpAds 引用的 baidumap.js 来自 app2smile，MIT，见 `scripts/app2smile/LICENSE.md`）。上游没有许可证或 NOTICE 时不另造一份，只保留原作者署名和来源。脚本文件头保持原样，不做修改。

上游脚本地址连续 3 次校验失败，或返回 404 时，覆写会改用本仓库 raw 镜像；上游恢复后自动改回。校验不通过（404、下载错误、HTML、空文件、过小、或 JavaScript 语法错误）时保留上一份可用副本，不会把脚本或覆写清空。每个上游单独处理，一个失败不影响其他来源。

这份镜像只供个人备份，不能拿去当成自己的作品再分发。著作权归原作者所有。

| 覆写 | 原作者 | 上游 | 安装 |
| --- | --- | --- | --- |
| [YouTube.stoverride](YouTube.stoverride) | [Maasea](https://github.com/Maasea/sgmodule) | https://raw.githubusercontent.com/Maasea/sgmodule/master/YouTube.Enhance.sgmodule | [一键安装](stash://install-override?url=https%3A%2F%2Fraw.githubusercontent.com%2Fwsc82%2FClash%2Fmain%2FStash%2FYouTube.stoverride) |
| [StartUpAds.stoverride](StartUpAds.stoverride) | [ddgksf2013](https://github.com/ddgksf2013/Rewrite) | https://ddgksf2013.top/rewrite/StartUpAds.conf | [一键安装](stash://install-override?url=https%3A%2F%2Fraw.githubusercontent.com%2Fwsc82%2FClash%2Fmain%2FStash%2FStartUpAds.stoverride) |
| [CheLaiLeAds.stoverride](CheLaiLeAds.stoverride) | [ddgksf2013](https://github.com/ddgksf2013/Rewrite) | https://raw.githubusercontent.com/ddgksf2013/Rewrite/refs/heads/master/AdBlock/CheLaiLeAds.conf | [一键安装](stash://install-override?url=https%3A%2F%2Fraw.githubusercontent.com%2Fwsc82%2FClash%2Fmain%2FStash%2FCheLaiLeAds.stoverride) |
| [AmapAds.stoverride](AmapAds.stoverride) | [ddgksf2013](https://github.com/ddgksf2013/Rewrite) | https://raw.githubusercontent.com/ddgksf2013/Rewrite/master/AdBlock/AmapAds.conf | [一键安装](stash://install-override?url=https%3A%2F%2Fraw.githubusercontent.com%2Fwsc82%2FClash%2Fmain%2FStash%2FAmapAds.stoverride) |
| [BiliBiliAdsLite.stoverride](BiliBiliAdsLite.stoverride) | [ddgksf2013](https://github.com/ddgksf2013/Rewrite) | https://ddgksf2013.top/rewrite/BiliBiliAdsLite.conf | [一键安装](stash://install-override?url=https%3A%2F%2Fraw.githubusercontent.com%2Fwsc82%2FClash%2Fmain%2FStash%2FBiliBiliAdsLite.stoverride) |
| [MiGuVideoAds.stoverride](MiGuVideoAds.stoverride) | [ddgksf2013](https://github.com/ddgksf2013/Rewrite) | https://ddgksf2013.top/rewrite/MiGuVideoAds.conf | [一键安装](stash://install-override?url=https%3A%2F%2Fraw.githubusercontent.com%2Fwsc82%2FClash%2Fmain%2FStash%2FMiGuVideoAds.stoverride) |

## 感谢

感谢原作者 Maasea、ddgksf2013 等。规则与脚本的著作权归原作者所有。这里只做 Stash 格式转换，并按上游原文保留一份备份，不把原作者的作品改头换面。

## 许可证

- `YouTube.stoverride` 转换自 [Maasea](https://github.com/Maasea/sgmodule) 的配置，许可证为 Apache-2.0。上游没有 NOTICE 文件。全文见 [scripts/Maasea/LICENSE](scripts/Maasea/LICENSE)。脚本镜像在 `scripts/Maasea/`，与上游字节相同。 protobuf 描述已经编进脚本，没有单独的描述文件或参数文件。youtube.request.js 在 initplayback 命中缓存 key 时会请求 https://init-stream.maasea.workers.dev/ 。这是动态接口，地址写死在脚本里；镜像必须与上游字节一致，所以不能把这个地址改成本仓库。上游脚本文件被删时，去广告脚本仍从本仓库加载；该接口若同时不可用，只有这段 initplayback 重定向会受影响。
- `StartUpAds.stoverride` 转换自 [ddgksf2013](https://github.com/ddgksf2013/Rewrite) 的配置。上游仓库没有 SPDX 许可证文件。配置原文要求「如需引用请注明出处」。脚本镜像在 `scripts/ddgksf2013/`，与上游字节相同。 其中 baidumap.js 来自 app2smile/rules（MIT），许可证全文见 scripts/app2smile/LICENSE.md。一条 script-analyze-echo-response（12306）转成了 request 脚本。以分号开头的禁用行被跳过，记在覆写末尾。
- `CheLaiLeAds.stoverride` 转换自 [ddgksf2013](https://github.com/ddgksf2013/Rewrite) 的配置。上游仓库没有 SPDX 许可证文件。配置原文要求「如需引用请注明出处」。脚本镜像在 `scripts/ddgksf2013/`，与上游字节相同。
- `AmapAds.stoverride` 转换自 [ddgksf2013](https://github.com/ddgksf2013/Rewrite) 的配置。上游仓库没有 SPDX 许可证文件。配置原文要求「如需引用请注明出处」。脚本镜像在 `scripts/ddgksf2013/`，与上游字节相同。 脚本匹配了 amdc.m.taobao.com，但上游 hostname 列表里没有这个域名。Stash 不会对它做 MitM，除非在别处加上该主机。
- `BiliBiliAdsLite.stoverride` 转换自 [ddgksf2013](https://github.com/ddgksf2013/Rewrite) 的配置。上游仓库没有 SPDX 许可证文件。配置原文要求「如需引用请注明出处」。脚本镜像在 `scripts/ddgksf2013/`，与上游字节相同。
- `MiGuVideoAds.stoverride` 转换自 [ddgksf2013](https://github.com/ddgksf2013/Rewrite) 的配置。上游仓库没有 SPDX 许可证文件。配置原文要求「如需引用请注明出处」。脚本镜像在 `scripts/ddgksf2013/`，与上游字节相同。

有 SPDX 许可证的上游，该许可证覆盖对应的转换文件和从上游镜像过来的脚本。没有许可证文件的上游只按原作者要求署名。这不改变本仓库里其他文件的许可。
