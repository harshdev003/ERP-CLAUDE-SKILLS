# Cloud Armor rules the monitor knows

The script reads **only the JSON block below**: the rule numbers, crawler list and thresholds
live here so a new rule is a docs change, not a code change. Each run also reads the live
`aura-web-edge` policy and prints a warning for any priority this file doesn't list, rather
than guessing what it does.

## Rules (live policy, 2026-11-10)

| Priority | Action | What it is |
|---|---|---|
| 100–149 | deny | Incident IP blocks (`eng:block-ip`); present only while a block is live |
| 200 | deny | Scanner paths (`.env`, `.git`, WordPress, …). A client that hits it is probing, not browsing |
| 1000–1090 | deny (most in preview) | OWASP CRS families; 1050 (scanner detection) is enforced |
| 2900 | deny | Search crawlers with no audience (Baidu, Petal) |
| 2910 | throttle | AI-training crawlers |
| 3000, 3010 | throttle | Tencent / Alibaba Cloud crawl |
| 3100 | throttle (preview) | Coach-page scraper |
| 3200 | rate-based ban | Redeem-invite check |
| 3300, 3310, 3320, 3390 | throttle | Cookiebot, ShapBot, SEO tools, other self-declared bots |
| 3900 | allow | Temporary: coach-web `/auraServices` skips the per-IP limit |
| 4000 | rate-based ban | Per-IP throttle (300/min, ban at 2000 per 10 min) |
| 2147483647 | allow | Default |

## Real-user checks

A window **flags** (exit 1) when any of these holds:

- **App denied:** a deny by any rule but 200 whose user agent is ours (`app_ua`). Threshold 0.
- **API throttled:** rule-4000 denies on `/api` or `/auraServices` above `api_throttle`, leaving
  out clients that also hit rule 200, self-declared bots and link-preview fetchers
  (`non_user_ua`), and requests to the load balancer's bare IP.
- **Coach denied:** coach-web denies by any rule but 200 above `coach_deny`; a rule-200 client's
  rule-4000 hits don't count.
- **Static throttled** (closes the ENGBACK-2502 gap): rule-4000 denies of www static assets
  (`static_path`) above `static_throttle`, with the same exclusions as the API check: link
  previews fetch OG images and scanners fetch favicons, and neither is a person.
- **5xx:** 5xx responses outside `/blog` (Webflow's upstream) above `e5xx`.

`daily` uses the `daily` thresholds over 24 h. They are starting values: tune them here.

## Compare re-checks

After a rule change, nothing in `must_not_deny` may be denied. Each entry is a user-agent or a
path regex; a deny that matches one is a flag, except a rule-200 deny (a scanner path is probing
whoever sends it, as in the watch's app check).

```json
{
  "project": "firebase-auratech16",
  "policy": "aura-web-edge",
  "backend": "aura-web",
  "known_priorities": [[100, 149], 200, [1000, 1090], 2900, 2910, 3000, 3010, 3100, 3200, 3300, 3310, 3320, 3390, 3900, 4000, 2147483647],
  "scanner_rule": 200,
  "throttle_rule": 4000,
  "app_ua": "^(AuraConsumerApp|Aura|Aurie)/",
  "non_user_ua": "(?i)bot|crawler|spider|facebookexternalhit|whatsapp|skypeuripreview|iframely|embedly",
  "api_path": "^/(api|auraServices)(/|$)",
  "static_host": "^www\\.",
  "static_path": "^/_next/|\\.(png|jpe?g|gif|webp|avif|svg|ico|woff2?|ttf|otf|css|js)$",
  "blog_path": "^/blog(/|$)",
  "thresholds": {
    "window": {"app_ua": 0, "api_throttle": 20, "coach_deny": 20, "static_throttle": 20, "e5xx": 20},
    "daily": {"app_ua": 0, "api_throttle": 200, "coach_deny": 200, "static_throttle": 200, "e5xx": 200}
  },
  "must_not_deny": {
    "link previews": {"ua": "(?i)facebookexternalhit|twitterbot|slackbot|linkedinbot|discordbot|whatsapp|telegrambot|skypeuripreview|iframely|embedly"},
    "AASA-Bot": {"ua": "(?i)aasa-bot"},
    "search engines": {"ua": "(?i)googlebot|bingbot|applebot|duckduckbot|yandexbot"},
    "our apps": {"ua": "^(AuraConsumerApp|Aura|Aurie)/"},
    "/.well-known/": {"path": "^/\\.well-known/"}
  },
  "log_limit": 20000
}
```
