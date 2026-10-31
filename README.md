# Clash

这里是 Clash / mihomo 用的规则，从 [wsc82/Surge](https://github.com/wsc82/Surge) 自动转过来的。

每天凌晨 0:30（北京时间）会拉一次 Surge 仓库里的六个 `.list`，转成 classical 的 yaml。想改规则就去 Surge 那边改，别在这个仓库里手改 `Ruleset/*.yaml`，下次同步会盖掉。也可以在 Actions 里手动跑 Sync Surge rules。不再同步 JP：上游 `Ruleset/JP.list` 已删除，`.jp` 跟 Global 走。

Surge 里 mihomo 不认识的规则（比如 `USER-AGENT`、`URL-REGEX`、`RULE-SET`）转换时会丢掉，不会写进 yaml。

## 订阅地址

Global：

```
https://raw.githubusercontent.com/wsc82/Clash/main/Ruleset/Global.yaml
```

Domestic：

```
https://raw.githubusercontent.com/wsc82/Clash/main/Ruleset/Domestic.yaml
```

ApplePush（APNs 域名和官方 IP。Surge 里目前是备用，配置没有引用）：

```
https://raw.githubusercontent.com/wsc82/Clash/main/Ruleset/ApplePush.yaml
```

YouTube（YouTube 与 Google 视频 CDN）：

```
https://raw.githubusercontent.com/wsc82/Clash/main/Ruleset/YouTube.yaml
```

Telegram：

```
https://raw.githubusercontent.com/wsc82/Clash/main/Ruleset/Telegram.yaml
```

AI（Cursor、Grok、Muse、OpenAI、Perplexity 与 X）：

```
https://raw.githubusercontent.com/wsc82/Clash/main/Ruleset/AI.yaml
```

## mihomo / Clash Verge

策略组里要有 `Self` 和 `YouTube`。Domestic 走直连。Telegram、AI 跟 Global 一样走 `Self`。YouTube、Telegram、AI 写在 Global 前面，因为 Global 里也有这几段。ApplePush 只放在 `rule-providers` 里，示例规则不挂它，和 Surge 一样先备用。

```yaml
rule-providers:
  YouTube: {type: http, behavior: classical, format: yaml, url: https://raw.githubusercontent.com/wsc82/Clash/main/Ruleset/YouTube.yaml, path: ./ruleset/YouTube.yaml, interval: 86400}
  Telegram: {type: http, behavior: classical, format: yaml, url: https://raw.githubusercontent.com/wsc82/Clash/main/Ruleset/Telegram.yaml, path: ./ruleset/Telegram.yaml, interval: 86400}
  AI: {type: http, behavior: classical, format: yaml, url: https://raw.githubusercontent.com/wsc82/Clash/main/Ruleset/AI.yaml, path: ./ruleset/AI.yaml, interval: 86400}
  Global: {type: http, behavior: classical, format: yaml, url: https://raw.githubusercontent.com/wsc82/Clash/main/Ruleset/Global.yaml, path: ./ruleset/Global.yaml, interval: 86400}
  Domestic: {type: http, behavior: classical, format: yaml, url: https://raw.githubusercontent.com/wsc82/Clash/main/Ruleset/Domestic.yaml, path: ./ruleset/Domestic.yaml, interval: 86400}
  ApplePush: {type: http, behavior: classical, format: yaml, url: https://raw.githubusercontent.com/wsc82/Clash/main/Ruleset/ApplePush.yaml, path: ./ruleset/ApplePush.yaml, interval: 86400}

rules:
  - RULE-SET,YouTube,YouTube
  - RULE-SET,Telegram,Self
  - RULE-SET,AI,Self
  - RULE-SET,Global,Self
  - RULE-SET,Domestic,DIRECT
  - GEOIP,CN,DIRECT
  - MATCH,Self
```
