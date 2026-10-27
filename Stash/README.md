# Stash 覆写

这些文件由上游配置自动转换成 Stash 覆写。每天 00:45（北京时间）同步一次。请不要手工修改本目录里的文件，下次同步会覆盖修改。

覆写里的脚本地址默认仍指向原作者。同步会把每个脚本按字节下载到 `scripts/<作者>/`，旁边放上游许可证（Maasea 为 Apache-2.0，见 `scripts/Maasea/LICENSE`）。上游没有 NOTICE 时不另造一份。脚本文件头保持原样，不做修改。

上游脚本地址连续 3 次校验失败，或返回 404 时，覆写会改用本仓库 raw 镜像；上游恢复后自动改回。校验不通过（404、下载错误、HTML、空文件、过小、或 JavaScript 语法错误）时保留上一份可用副本，不会把脚本或覆写清空。每个上游单独处理，一个失败不影响其他来源。

这份镜像只供个人备份，不能拿去当成自己的作品再分发。著作权归原作者所有。

| 覆写 | 原作者 | 上游 | 安装 |
| --- | --- | --- | --- |
| [YouTube.stoverride](YouTube.stoverride) | [Maasea](https://github.com/Maasea/sgmodule) | https://raw.githubusercontent.com/Maasea/sgmodule/master/YouTube.Enhance.sgmodule | [一键安装](stash://install-override?url=https%3A%2F%2Fraw.githubusercontent.com%2Fwsc82%2FClash%2Fmain%2FStash%2FYouTube.stoverride) |

## 感谢

感谢原作者 Maasea 等。规则与脚本的著作权归原作者所有。这里只做 Stash 格式转换，并按上游原文保留一份备份，不把原作者的作品改头换面。

## 许可证

- `YouTube.stoverride` 转换自 [Maasea](https://github.com/Maasea/sgmodule) 的配置，许可证为 Apache-2.0。上游没有 NOTICE 文件。全文见 [scripts/Maasea/LICENSE](scripts/Maasea/LICENSE)。脚本镜像在 `scripts/Maasea/`，与上游字节相同。 protobuf 描述已经编进脚本，没有单独的描述文件或参数文件。youtube.request.js 在 initplayback 命中缓存 key 时会请求 https://init-stream.maasea.workers.dev/ 。这是动态接口，地址写死在脚本里；镜像必须与上游字节一致，所以不能把这个地址改成本仓库。上游脚本文件被删时，去广告脚本仍从本仓库加载；该接口若同时不可用，只有这段 initplayback 重定向会受影响。

上述许可证覆盖对应的转换文件和从上游镜像过来的脚本，不改变本仓库里其他文件的许可。
