# memory/agent-log.md —— 跨会话唯一档案

## 状态段

STATE: task=归档封存+推送GitHub | level=L2-F | route=完整9步(文档写作型) | confirm=已问(可见性/仓库创建方式均获授权) | gates_passed=归档commit+push完成 | last_errpath=无
权威承载=本文件（memory/agent-log.md）
项目状态：**已封存**（2026-10-08 归档完成）。版本 v1.3 定版，代码未改动。
远程仓库：https://github.com/0range-Cat/git-visual-tool （public，main 分支）
归档文档：`归档/` 00-08 共 9 份，入口=00-归档总览.md
回滚基线：commit 587e184（归档前现状基线）→ 归档 commit → push 完成
注意：`.gitattributes` 锁定 `*.bat eol=crlf`（闪退教训），接手者勿删；git 推送需走本地代理（直连 443 被重置），用 `-c http.proxy=http://127.0.0.1:7897` 单次参数。

## 教训区

- [环境] GitHub 直连 github.com:443 会 Connection reset/超时，但本机 127.0.0.1:7897 有可用代理（curl -x 可通）。git 用 `-c http.proxy=... -c https.proxy=...` 单次参数走代理即可推送；不写全局配置避免影响其他仓库。（2026-10-08）
- [环境] Windows 凭据管理器存有 github.com 的 0range-Cat 令牌（git credential fill 可验证），无需 gh CLI 也能推 GitHub——用 API token 调 REST 建仓库即可。（2026-10-08）

## 偏好段

- 文档写作默认中文；史料文件（折腾记录.md/博客文章.md）保持原样不改。

## 流水区

- 2026-10-08 归档任务：通读全部项目文件 → git init(main) + .gitignore/.gitattributes + 基线 commit 587e184 → 编写 归档/00-08 九份文档 → 建 memory/ → commit → 创建 GitHub 仓库 git-visual-tool(public) 并推送 → push 验证通过。验证：git log/ls-remote 远程核对。未验证项：GUI 端到端冒烟（归档未改代码，留接手者）。
