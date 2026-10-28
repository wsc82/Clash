# Clash

这里是 Clash / mihomo 用的规则，从 [wsc82/Surge](https://github.com/wsc82/Surge) 自动转过来的。

每天凌晨 0:30（北京时间）会拉一次 Surge 仓库里的三个 `.list`，转成 classical 的 yaml。想改规则就去 Surge 那边改，别在这个仓库里手改 `Ruleset/*.yaml`，下次同步会盖掉。不再同步 JP：上游 `Ruleset/JP.list` 已删除，`.jp` 跟 Global 走。

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

## mihomo / Clash Verge

策略组里要有 `TOP`。Domestic 走直连。ApplePush 只放在 `rule-providers` 里，示例规则不挂它，和 Surge 一样先备用。

```yaml
rule-providers:
  Global: {type: http, behavior: classical, format: yaml, url: https://raw.githubusercontent.com/wsc82/Clash/main/Ruleset/Global.yaml, path: ./ruleset/Global.yaml, interval: 86400}
  Domestic: {type: http, behavior: classical, format: yaml, url: https://raw.githubusercontent.com/wsc82/Clash/main/Ruleset/Domestic.yaml, path: ./ruleset/Domestic.yaml, interval: 86400}
  ApplePush: {type: http, behavior: classical, format: yaml, url: https://raw.githubusercontent.com/wsc82/Clash/main/Ruleset/ApplePush.yaml, path: ./ruleset/ApplePush.yaml, interval: 86400}

rules:
  - RULE-SET,Global,TOP
  - RULE-SET,Domestic,DIRECT
  - GEOIP,CN,DIRECT
  - MATCH,TOP
```
