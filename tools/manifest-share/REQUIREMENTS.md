# Manifest Share 需求文档

## 1. 背景

`media-pull` 通过远程 `manifest_url` 获取媒体 URL 清单。为了在本地快速创建、修改并公开一个可被 GitHub Actions、服务器或其他客户端通过 HTTP 获取的 manifest，需要提供一个轻量、临时、自包含的文本分享工具。

该工具使用 MicroBin 提供文本存储和 Raw 内容访问能力，并使用 Cloudflare Quick Tunnel 将本机服务临时暴露到公网。

本工具定位为 `media-pull` 的开发/辅助工具，不属于媒体下载、镜像构建或 GHCR 发布的核心链路。

## 2. 项目位置与命名

固定目录：

```text
tools/manifest-share/
```

主命令：

```text
tools/manifest-share/microbin-start
```

默认本地端口：

```text
18787
```

## 3. 核心目标

工具需要满足以下目标：

1. 一条命令启动本地 MicroBin。
2. 自动建立 Cloudflare Quick Tunnel。
3. 自动获得随机 `*.trycloudflare.com` 公网地址。
4. 用户可以通过浏览器创建公开文本。
5. 文本可以通过 `/raw/<id>` 作为纯文本 URL 获取。
6. `/list` 页面中每条文本记录提供独立的 `Copy` 操作（本文档中称为 Copy URL 功能）。
7. `Copy` 按钮必须复制当前 Cloudflare 公网域名拼接后的完整 Raw URL。
8. 所有业务数据、日志、PID、Tunnel 临时状态都限制在 `tools/manifest-share/` 内。
9. 不依赖固定域名，不依赖 Cloudflare 账号。
10. 不使用 MicroBin 的 `MICROBIN_PUBLIC_PATH`。

## 4. 目录隔离要求

运行时文件必须位于脚本所在目录。

预期目录结构：

```text
tools/manifest-share/
├── microbin-start
├── REQUIREMENTS.md
├── .gitignore
│
├── data/                  # MicroBin 持久化数据
├── cloudflare-home/       # cloudflared 独立 HOME
├── cloudflared.log        # Tunnel 日志
├── cloudflared.pid        # Tunnel PID
├── url                    # 当前公网 URL
└── admin_password         # 当前 MicroBin 管理员随机密码
```

不得主动创建以下项目级或用户级配置：

```text
~/.cloudflared/
~/.local/share/manifest-share/
项目根目录下的临时文件
Docker named volume
```

允许 Docker 自身保存以下内容，因为它们属于 Docker 运行时基础设施，无法由项目目录完全承载：

- MicroBin Docker image cache
- Docker daemon 元数据
- 容器运行时元数据

MicroBin 容器应使用 bind mount：

```text
tools/manifest-share/data/
    ↓
/app/microbin_data
```

不得使用 Docker named volume 保存 MicroBin 数据。

## 5. 启动要求

执行：

```bash
./tools/manifest-share/microbin-start
```

等价于：

```bash
./tools/manifest-share/microbin-start start
```

启动顺序必须是：

```text
Cloudflare Tunnel
        ↓
获得公网 URL
        ↓
MicroBin
        ↓
把公网 URL 注入 Footer JS
```

即必须先拿到 Quick Tunnel 的公网地址，再启动 MicroBin，并在启动 MicroBin 容器时把该地址通过 `MICROBIN_FOOTER_TEXT` 注入的脚本写入页面，暴露为 `window.__MANIFEST_SHARE_PUBLIC_ORIGIN__`（见第 10 节）。不得先启动 MicroBin 再补写公网地址。

启动过程必须依次完成：

1. 检查 `docker`。
2. 检查 `cloudflared`。
3. 检查 `curl`。
4. 确认 Docker daemon 可用。
5. 启动 Cloudflare Quick Tunnel。
6. 从 `cloudflared` 输出中解析 `https://*.trycloudflare.com`。
7. 保存公网地址到 `url`。
8. 创建 `data/`。
9. 启动 MicroBin，并把上一步获得的公网地址通过 Footer JS 注入。
10. 等待 `http://127.0.0.1:18787` 可访问。
11. 检查公网地址基本可访问。
12. 在 macOS 上将公网地址复制到剪贴板。
13. 默认在 macOS 浏览器中打开公网地址。

## 6. 默认端口

默认绑定：

```text
127.0.0.1:18787
```

MicroBin 容器内部仍使用：

```text
8080
```

映射关系：

```text
127.0.0.1:18787
        ↓
container:8080
```

只绑定 `127.0.0.1`，不得将 MicroBin 直接暴露到宿主机 LAN。

允许通过环境变量临时覆盖：

```bash
MANIFEST_SHARE_PORT=18888 ./microbin-start
```

## 7. Cloudflare Quick Tunnel

Tunnel 使用：

```bash
cloudflared tunnel --url http://127.0.0.1:18787
```

公网地址格式：

```text
https://<random>.trycloudflare.com
```

要求：

- 不要求 Cloudflare 登录。
- 不要求自有域名。
- 不创建 Named Tunnel。
- 不读取用户已有的 `~/.cloudflared/config.yaml`。
- `cloudflared` 的 HOME 必须定向到：
  `tools/manifest-share/cloudflare-home/`。
- 当前 Tunnel URL 必须保存到：
  `tools/manifest-share/url`。

Quick Tunnel 生命周期与 `cloudflared` 进程绑定。

当已有 Tunnel 正常运行时，再次执行 `microbin-start` 应复用现有 Tunnel，不主动生成新 URL。

以下操作允许产生新的随机 URL：

```text
restart
stop 后再次 start
cloudflared 异常退出后再次 start
```

## 8. MicroBin 配置原则

配置应尽可能少，只显式设置项目确实需要改变的行为。

### 8.1 必须设置

```text
MICROBIN_DEFAULT_PRIVACY=public
```

新建文本默认公开并出现在 `/list`。

```text
MICROBIN_DISABLE_TELEMETRY=true
```

该临时工具不主动发送 MicroBin telemetry。

```text
MICROBIN_HIDE_FOOTER=false
```

必须显式关闭 footer 隐藏，`Copy URL` 功能（见第 10 节）依赖 `MICROBIN_FOOTER_TEXT` 注入的脚本渲染在 footer 中才能生效。

```text
MICROBIN_ADMIN_USERNAME=admin
MICROBIN_ADMIN_PASSWORD=<每次启动随机生成>
```

MicroBin 自带管理界面 `/admin`，上游默认管理员密码固定为 `m1cr0b1n`，属于已知的公开默认凭据。本工具每次真正创建新的 MicroBin 容器（而非复用已运行容器）时，必须生成一个新的高强度随机密码并通过 `MICROBIN_ADMIN_PASSWORD` 注入，使默认密码彻底失效。不得省略该变量或使用固定/可预测的密码。

生成的密码仅保存在 `tools/manifest-share/admin_password`（运行时文件，不提交 Git），并在启动完成后打印一次，方便用户登录 `/admin`。复用已运行容器时不生成新密码。

### 8.2 Encryption

本工具不提供 Encryption 功能。

必须明确关闭：

```text
MICROBIN_ENCRYPTION_CLIENT_SIDE=false
MICROBIN_ENCRYPTION_SERVER_SIDE=false
```

这里不能仅通过“删除配置项”表达关闭状态，因为 MicroBin 不同版本的默认值可能变化；本工具要求行为明确。

### 8.3 明确不配置

不得设置：

```text
MICROBIN_PUBLIC_PATH
MICROBIN_QR
MICROBIN_HASH_IDS
```

ID 格式使用 MicroBin 上游默认值，不强制指定。

其中默认隐私级别单独通过：

```text
MICROBIN_DEFAULT_PRIVACY=public
```

控制。

其他 MicroBin 行为尽量使用上游默认值，以减少维护成本。

## 9. Raw URL

MicroBin 文本 Raw 路径为：

```text
/raw/<id>
```

例如：

```text
/raw/dBGYNe
```

由于不设置 `MICROBIN_PUBLIC_PATH`，页面中的 Raw 链接保持相对路径。

当用户通过 Cloudflare 地址访问页面时：

```text
https://example-random.trycloudflare.com/list
```

浏览器会把：

```text
/raw/dBGYNe
```

解析为：

```text
https://example-random.trycloudflare.com/raw/dBGYNe
```

因此无需将 Cloudflare URL 回写到 MicroBin 的核心配置（如 `MICROBIN_PUBLIC_PATH`）。

`Copy URL` 功能是例外：它需要知道当前公网地址才能生成域名正确的分享链接（见第 10 节），因此由启动脚本自动将该地址写入注入的 footer 脚本，并在地址变化时自动重建 MicroBin 容器，用户无需手动干预。

## 10. Copy URL 功能

### 10.1 页面位置

在 `/list` 的 Uploads 表格与 URL Redirects 表格中，每行第二个表格单元格（`<td>`，位于 Key 列之后）原内容分别为：

```text
Uploads 表格:       (空)
URL Redirects 表格: Copy（复制相对路径 /url/<id> 的原生按钮）
```

必须清空该单元格原有内容，并用统一的 `Copy` 按钮替换：

```text
Uploads 表格:       Copy
URL Redirects 表格: Copy
```

不得新增独立单元格或表头列，也不得把 `Copy` 按钮放进 `Remove` 所在单元格。两个表格都必须具备该功能，不得只在 Uploads 表格生效。

单条记录详情页 `/upload/<id>` 使用的是一行内联操作栏：

```text
Copy Text | Raw Text Content | Edit | Remove
```

而不是表格结构，因此没有第二个 `<td>` 可用。该页面必须在 `Remove` 链接之后追加同一个 `Copy` 按钮：

```text
Copy Text | Raw Text Content | Edit | Remove Copy
```

### 10.2 复制内容

对于 ID：

```text
dBGYNe
```

如果当前公网 Quick Tunnel 地址为：

```text
https://abc-def.trycloudflare.com
```

无论当前页面是通过该公网地址访问，还是通过 `http://127.0.0.1:18787` 本地访问，点击 Uploads 表格中的 `Copy` 按钮都必须得到：

```text
https://abc-def.trycloudflare.com/raw/dBGYNe
```

点击 URL Redirects 表格中的 `Copy` 按钮都必须得到：

```text
https://abc-def.trycloudflare.com/url/dBGYNe
```

点击 `/upload/dBGYNe` 详情页中 `Remove` 后面的 `Copy` 按钮，同样必须得到：

```text
https://abc-def.trycloudflare.com/raw/dBGYNe
```

不得复制：

```text
http://127.0.0.1:18787/raw/dBGYNe
```

也不得复制仅包含：

```text
/raw/dBGYNe
```

### 10.3 实现行为要求

Copy 按钮复制内容的域名部分必须固定使用当前 Quick Tunnel 的公网地址，而不是页面当前访问所用的 origin。

实现方式：

```text
启动脚本在拿到 Quick Tunnel 地址后
        ↓
将该地址写入 MicroBin 容器环境变量
        ↓
通过 MICROBIN_FOOTER_TEXT 注入的脚本
将其暴露为 window.__MANIFEST_SHARE_PUBLIC_ORIGIN__
        ↓
Copy 拼接：
window.__MANIFEST_SHARE_PUBLIC_ORIGIN__ + 页面 anchor 的路径部分（/raw/<id> 或 /url/<id>）
```

路径部分仍然取自浏览器已解析的 anchor（避免启动脚本重复拼接路径规则），只有域名部分由脚本注入的公网地址决定。

当 Quick Tunnel 地址变化时（重启、旧 Tunnel 异常退出后重新创建等），MicroBin 容器必须同步重新创建以更新该环境变量，不得保留旧容器继续使用过期域名。

### 10.4 用户反馈

点击后：

```text
Copy
```

临时变为：

```text
Copied
```

约 1 秒后恢复。

复制失败时显示：

```text
Failed
```

### 10.5 Clipboard 兼容

优先使用：

```text
navigator.clipboard.writeText()
```

当 Clipboard API 不可用时，应提供 textarea + `document.execCommand('copy')` fallback。

### 10.6 表格宽度

MicroBin 原生模板对 Uploads 和 URL Redirects 两个表格都设置了 `min-width: 720px`，配合 `white-space: nowrap` 会在较窄视口下产生横向滚动条。

不再通过全局 CSS（`table { min-width: unset !important; ... }` / `th, td { white-space: normal !important; ... }`）覆盖上游样式：该规则作用于站内所有表格（包括 `/admin` 等页面），影响范围超出本工具实际需要改动的内容，不建议保留。

追加 `Copy` 按钮时应尽量不引入新的横向滚动，但不强制覆盖 MicroBin 原生表格样式。

## 11. 数据持久化

执行：

```bash
./microbin-start stop
```

必须：

- 停止 Cloudflare Tunnel。
- 删除/停止 MicroBin 临时容器。
- 保留 `data/`。
- 下次启动后此前创建的 MicroBin 内容仍然存在。

MicroBin 容器本身不是持久化对象。

持久化边界为：

```text
tools/manifest-share/data/
```

## 12. 命令接口

### start

```bash
./microbin-start
./microbin-start start
```

启动 MicroBin 和 Quick Tunnel。

### stop

```bash
./microbin-start stop
```

停止服务，保留数据。

### restart

```bash
./microbin-start restart
```

停止后重新启动。

预期 Cloudflare Quick Tunnel URL 会变化。

### status

```bash
./microbin-start status
```

显示：

- 工具目录
- 当前本地端口
- MicroBin 状态
- Cloudflare Tunnel 状态
- 当前公网 URL

### url

```bash
./microbin-start url
```

只输出当前公网 URL，方便脚本组合，例如：

```bash
MANIFEST_HOST="$(./microbin-start url)"
```

### logs

```bash
./microbin-start logs
```

显示：

- 最近的 MicroBin 容器日志
- 最近的 cloudflared 日志

### clean

```bash
./microbin-start clean
```

停止服务并删除运行时临时文件：

```text
cloudflare-home/
cloudflared.log
cloudflared.pid
url
admin_password
```

必须保留：

```text
data/
```

### purge

```bash
./microbin-start purge
```

在 `clean` 基础上进一步删除：

```text
data/
```

该命令代表彻底删除本工具保存的 MicroBin 内容。

## 13. Git 管理要求

运行时数据不得提交到 Git。

`tools/manifest-share/.gitignore` 至少忽略：

```gitignore
data/
cloudflare-home/
cloudflared.log
cloudflared.pid
url
admin_password
```

应提交：

```text
microbin-start
REQUIREMENTS.md
.gitignore
```

## 14. 依赖

宿主机要求：

```text
Docker
cloudflared
curl
```

macOS 可选：

```text
pbcopy
open
```

如果 `pbcopy` 不存在，仅跳过自动复制。

如果 `open` 不存在，仅跳过自动打开浏览器。

工具本身不得要求：

```text
Node.js
Python
Go
Cloudflare Account
Cloudflare API Token
自有域名
```

## 15. Docker 行为

MicroBin 默认镜像固定为具体版本号，不长期使用 `:latest`：

```text
danielszabo99/microbin:2.1.4
```

升级版本需要显式修改该默认值（或临时通过下方环境变量覆盖），不得静默跟随上游 `:latest` 滚动更新。

允许通过：

```bash
MICROBIN_IMAGE=<image> ./microbin-start
```

覆盖。

容器名称默认：

```text
media-pull-manifest-share
```

容器应使用：

```text
--rm
```

停止后不保留已退出容器。

宿主机用户 UID/GID 应传递给容器进程，以便 bind-mounted `data/` 可直接由当前用户管理，不要求对项目目录执行 `sudo chown`。

## 16. 错误处理

以下情况必须明确失败并返回非零退出码：

- Docker 命令不存在。
- Docker daemon 未运行。
- `cloudflared` 不存在。
- `curl` 不存在。
- MicroBin 在合理等待时间内未启动。
- `cloudflared` 启动后立即退出。
- 等待超时仍无法从日志中解析 Quick Tunnel URL。

MicroBin 启动失败时应打印 MicroBin 日志。

Cloudflare Tunnel 启动失败时应打印 cloudflared 日志。

公网健康检查短时间未通过但 Tunnel 进程仍运行时，可以给出 warning，而不是立即销毁已获得的 URL。

## 17. 安全边界

该工具的目标是临时分享 manifest 和普通公开文本，不用于秘密数据。

必须明确：

- `MICROBIN_DEFAULT_PRIVACY=public`。
- Encryption 被关闭。
- 获得 `trycloudflare.com` URL 的任何人都可以访问公开内容。
- 不应粘贴密码、Token、Cookie、私钥或其他秘密。
- Quick Tunnel 是临时开发能力，不提供生产 SLA。
- 如果未来需要长期固定 URL、访问控制或生产可靠性，应单独设计 Named Tunnel/固定域名方案。

MicroBin 上游版本自带管理界面（`/admin`）及固定的默认管理凭据（用户名 `admin`，密码 `m1cr0b1n`）。本工具通过每次启动生成随机高强度密码（见第 8.1 节）使该默认凭据失效，但公网使用时仍不应把该服务视为安全的秘密存储系统。

## 18. Cloudflare Quick Tunnel 限制

该工具接受 Quick Tunnel 的固有限制：

- hostname 随新的 `cloudflared` 进程变化。
- 不保证 SLA。
- 适用于开发、测试和临时分享。
- 当前 Cloudflare 文档规定 Quick Tunnel 存在并发请求数量限制。
- 不作为 `media-pull` 的永久基础设施。

## 19. 与 media-pull 的关系

典型工作流：

```text
本地创建 manifest
        ↓
MicroBin
        ↓
/raw/<id>
        ↓
Cloudflare Quick Tunnel
        ↓
https://*.trycloudflare.com/raw/<id>
        ↓
media-pull workflow 的 manifest_url
```

工具仅负责“生成一个临时可公网读取的 manifest URL”。

它不负责：

- 触发 GitHub Actions。
- 修改 `media-pull` workflow。
- 下载媒体。
- 验证媒体 URL。
- 构建 Docker image。
- 发布 GHCR。
- 保存 Cloudflare 固定域名。

## 20. 验收标准

### 启动

执行：

```bash
cd tools/manifest-share
./microbin-start
```

应成功获得：

```text
Local:
  http://127.0.0.1:18787

Public:
  https://*.trycloudflare.com
```

### 创建文本

通过公网页面创建：

```text
https://example.com/a.jpg
https://example.com/b.mp4
```

默认 privacy 必须为：

```text
public
```

### List

访问：

```text
https://*.trycloudflare.com/list
```

Uploads 与 URL Redirects 两个表格对应记录第二个单元格（Key 列之后）都应显示：

```text
Copy
```

### 详情页

访问：

```text
https://*.trycloudflare.com/upload/<id>
```

`Remove` 链接之后应新增：

```text
Copy
```

### Copy URL

在 Uploads 表格点击 `Copy` 按钮后：

```bash
pbpaste
```

应得到类似：

```text
https://*.trycloudflare.com/raw/dBGYNe
```

即使当前是通过 `http://127.0.0.1:18787/list` 本地访问该页面，复制结果的域名部分也必须是当前 Quick Tunnel 的 `*.trycloudflare.com` 地址，而不是 `127.0.0.1`。

### Raw

执行：

```bash
curl "$(pbpaste)"
```

返回内容应与创建的文本完全一致。

### 重启

执行：

```bash
./microbin-start restart
```

应满足：

- MicroBin 仍能读取原有 `data/`。
- 获得新的 Quick Tunnel URL。
- 新页面中的 `Copy` 按钮自动使用新的公网 hostname。
- 不需要修改 MicroBin Public Path。

### 停止

执行：

```bash
./microbin-start stop
```

应满足：

- Tunnel 不再运行。
- MicroBin 容器停止。
- `data/` 仍然存在。

## 21. 非目标

当前版本明确不实现：

- 固定 Cloudflare hostname。
- Named Tunnel。
- Cloudflare Access。
- 用户账号系统。
- MicroBin QR。
- MicroBin Encryption。
- Gist/Gitee 同步。
- 自动 Git commit。
- 自动触发 media-pull workflow。
- 为 Raw URL 增加 revision/version hash。
- 生产级高可用。
