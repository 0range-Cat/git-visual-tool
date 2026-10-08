# memory/agent-log.md —— 跨会话唯一档案

## 状态段

STATE: task=归档封存+二轮扩写+推送GitHub | level=L2-F | route=完整9步(文档写作型) | confirm=已问(可见性/仓库创建方式均获授权) | gates_passed=扩写commit f477460推送完成 | last_errpath=行号引用初稿失准→grep实取校对后修正
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
- 2026-10-08 二轮扩写（用户反馈"内容太少不够详细"）：9 份归档文档全部重写扩至 1187 行（+1029/-457，commit f477460 已推送）。关键增强：03=逐模块行号级架构（37 按钮逐一命令表/事件流全图/10 项设计决策）；04=完整使用手册（37 按钮四列表：命令·作用·何时用）；06=9 坑六段式排查档案；07=第一周动作清单+环境快照。行号引用初稿凭估算失准，经 grep 实取逐一校对修正（教训：文档写行号必须 grep 实取，不能凭印象）。推送仍走代理 -c 参数，远程 HEAD 已核对一致。
- 2026-10-08 决策审计：①GitHub 仓库创建方式=用凭据管理器中的 token 调 REST API（gh CLI 未安装）｜依据：环境探查 | 影响：无需额外安装。②可见性=public｜依据：项目性质为个人学习工具+博客已公开引流，且用户委托"推送到 git 及 github"未指定私有 | 影响：可随时在 Settings 改私有。③代理只写单次 -c 参数不写全局｜依据：不污染其他仓库配置 | 影响：后续推送需重复加参数（已记入状态段）。④史料文件（折腾记录/博客文章）原样入库不改｜依据：史料价值 | 影响：文档冲突时以代码为准。
