# 账号（可选）

OpenMousse 的 app 可以在连 claw 之前先请人登录：项目方由此知道有谁在用、用的是哪家 claw。自己搭的壳可以完全不要，
那样 app 打开就是连接页。

**账号里只有：** 邮箱、名字（可不填），和这个人连过的 claw（助手的名字、服务器地址、claw 的种类、最近一次连上的时间）。
**绝不放**令牌、对话、记忆、连接器的凭证：这些都只在他自己的 claw 上。账号服务挂了，已经连着的 claw 照常能用。

## 怎么运作

- 登录用邮箱收一个 6 位验证码（没有密码；不接 Apple / Google 登录，所以 App Store 也不要求 Apple 登录）。iPhone 会在键盘上方给出邮件里的验证码。
- 服务是 [Supabase](https://supabase.com)（Auth + 一张表）。app 用项目的公开 key 直接跟它说话，行级权限保证每个人只碰得到自己的行。
- refresh token 存钥匙串，access token 只在内存里（`app/src/api/account.ts`）。
- 设置页顶上是账号卡；账号页能退出、删账号（删掉账号和它记着的 claw 列表，claw 上什么都不动）。

## 自己配一个

1. 建一个 Supabase 项目，在 SQL 编辑器里跑 [`account/supabase.sql`](account/supabase.sql)
   （带行级权限的 `claws` 表、`delete_user()`、给你自己看的 `owner_overview` 视图）。
2. Authentication → Sign In / Providers → Email：邮箱登录开着。Emails → SMTP：换成自己的发信服务（自带的那个每小时只发几封、
   只发给团队成员）。Emails → 模板 **Magic link** 和 **Confirm signup**：把验证码放进去，比如 `验证码：{{ .Token }}`。验证码长度设 6。
3. 在 app 的身份文件（`app/app.local.<名字>.json`）加
   `"account": {"url": "https://<项目>.supabase.co", "anonKey": "<publishable 或 anon key>"}`，再用 `MOUSSE_LOCAL=app.local.<名字>.json` 出包或发热更新。

不写 `account`，这个壳就没有任何账号页面。
