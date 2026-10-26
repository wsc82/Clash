# Clash

这里是 Clash / mihomo 用的规则，从 [wsc82/Surge](https://github.com/wsc82/Surge) 自动转过来的。

每天会拉一次 Surge 仓库里的三个 `.list`，转成 classical 的 yaml。想改规则就去 Surge 那边改，别在这个仓库里手改 `Ruleset/*.yaml`，下次同步会盖掉。

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

JP：

```
https://raw.githubusercontent.com/wsc82/Clash/main/Ruleset/JP.yaml
```

## mihomo / Clash Verge

策略组里要有 `TOP` 和 `JP`。Domestic 走直连。

JP 必须写在 Global 上面，因为 Global 包含整个 `.jp` 后缀。

```yaml
rule-providers:
  JP: {type: http, behavior: classical, format: yaml, url: https://raw.githubusercontent.com/wsc82/Clash/main/Ruleset/JP.yaml, path: ./ruleset/JP.yaml, interval: 86400}
  Global: {type: http, behavior: classical, format: yaml, url: https://raw.githubusercontent.com/wsc82/Clash/main/Ruleset/Global.yaml, path: ./ruleset/Global.yaml, interval: 86400}
  Domestic: {type: http, behavior: classical, format: yaml, url: https://raw.githubusercontent.com/wsc82/Clash/main/Ruleset/Domestic.yaml, path: ./ruleset/Domestic.yaml, interval: 86400}

rules:
  - RULE-SET,JP,JP
  - RULE-SET,Global,TOP
  - RULE-SET,Domestic,DIRECT
  - GEOIP,CN,DIRECT
  - MATCH,TOP
```
