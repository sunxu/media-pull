# media-pull

从私有 GitHub 仓库读取私有 manifest，下载公开媒体，发布只含 `/data` 的公开 GHCR 镜像。最终 Dockerfile 由脚本生成，第一行是 `FROM scratch`；镜像中没有 shell、包管理器或操作系统。

## 架构

```text
manifest_url (workflow input) / MANIFEST_TOKEN (GitHub Secret)
                    |
                    v
 scripts/prepare.py: URL 去重 -> 并行续传/重试
                    |
                    v
 .media-build/context/layer-NNNN/data/... + generated Dockerfile
                    |
                    v
 FROM scratch + one COPY per <= 100,000,000-byte estimated layer group
                    |
                    v
 ghcr.io/OWNER/media-bundle:TAG -> media-pull -> local directory
```

下载发生在 `docker build` 之前。manifest、URL、Bearer token 和下载报告都不在 Docker build context 中，也不通过 `ARG`、`ENV` 或 `--build-arg` 传入，因此不会进入镜像 layer、配置或历史。

## Manifest 格式

使用 UTF-8 纯文本：每行一个公开 HTTPS URL，空行和以 `#` 开头的行会忽略。

```text
https://cdn.example.com/a.jpg
https://cdn.example.com/movie.mp4
```

- 默认只接受 HTTPS URL。
- 输出文件名使用 URL 路径的 basename。
- 完全重复的 URL 会去重。
- 输出文件名冲突时，所有冲突项会稳定地加入 URL 摘要，例如 `cover--a1b2...jpg`。

示例见 `manifest.example.txt`。

## GitHub 配置与发布

如果读取 manifest 需要 Bearer token，在私有仓库的 **Settings → Secrets and variables → Actions** 添加可选的 `MANIFEST_TOKEN`。它只发送给最初的 manifest 请求，不发送给媒体 URL，重定向时也不转发。

然后运行 **Actions → Build public media image → Run workflow**，填写必需的 `manifest_url`。默认并发数为 16；`tag` 留空时使用任务开始时的 UTC 时间（`YYYYMMDD-HHMMSS`），例如：

```text
ghcr.io/${{ github.repository_owner }}/media-bundle:20260831-133608
```

workflow 输入会保存在运行元数据中，因此 `manifest_url` 不应包含敏感签名参数。

GHCR 首次发布的 package 默认是 private。发布成功后，到用户或组织的 **Packages → media-bundle → Package settings → Change visibility → Public** 完成一次性设置。公开后任何人可匿名 pull；GitHub 目前警告 public package 不能再改回 private。参见 GitHub 官方的 [package 可见性配置](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility)。

## 本地构建

本地 manifest：

```bash
IMAGE=media-bundle:local JOBS=8 ./scripts/build-local.sh manifest.txt
```

私有远程 manifest：

```bash
MANIFEST_URL='https://example.com/private.txt' \
MANIFEST_TOKEN='token' \
IMAGE=media-bundle:local \
./scripts/build-local.sh
```

临时下载位于 `.media-cache`。未完成内容保存在 `.part` 文件中；重新运行会发送 HTTP `Range` 请求续传，服务器不支持 Range 时自动从头覆盖。由于没有内容校验值，成功文件不会跨构建复用，以免同一 URL 更新后仍使用旧内容。每个 URL 默认尝试 5 次并指数退避；服务器提供 `Content-Length`/`Content-Range` 时会检查下载是否完整。失败详情同时输出到终端和 `.media-build/report.md`。

## 提取

直接运行仓库脚本，目标目录必须为空：

```bash
./media-pull ghcr.io/OWNER/media-bundle:latest ./downloads
```

或安装为命令：

```bash
install -m 0755 media-pull /usr/local/bin/media-pull
media-pull ghcr.io/OWNER/media-bundle:latest ./downloads
```

它依次执行 `docker pull`、`docker create`、`docker cp /data/.` 和 `docker rm`。临时容器不会启动；scratch 镜像不需要可执行命令。

## Layer 分包与限制

脚本按稳定的输出路径顺序分包，每个分组的估算上限为 **100,000,000 bytes（100 MB）**，每组生成一个独立 `COPY` layer。估算包含按 512 bytes 对齐的文件内容、每文件 16 KiB 的 tar/目录余量和路径余量；同时限制路径深度与长度。单个媒体文件无法跨 OCI layer 后仍表现为一个普通文件，所以超过预算的单文件会明确失败；应在源端切分后再构建。

GitHub 官方记录的 GHCR 硬限制是每 layer 10 GB、上传超时 10 分钟；本项目的 100 MB 是更保守的自定义限制，不是 GHCR 的硬限制。实际 registry layer 是压缩 blob，压缩后大小会因文件格式而异。参见 GitHub 官方的 [Container registry troubleshooting](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry#troubleshooting)。

## 公开镜像的安全边界

- public image 的文件内容、文件名、大小、摘要和 layer 结构都视为公开；不要放秘密、私钥、token、私有元数据或未获授权内容。
- manifest 不包含内容校验值；HTTPS 和长度检查不能证明媒体内容的完整性或真实性，应信任 URL 来源。
- manifest URL/token 不进入镜像，但私有仓库协作者仍可能看到 Actions 日志和失败报告；报告按要求列出失败 URL，因此媒体 URL 本身也应可向这些协作者披露。
- 提取脚本拒绝非空目标目录，避免覆盖本地文件。

## 验证

```bash
python3 -m unittest discover -s tests -v
sh -n media-pull scripts/build-local.sh
```

若本机有 Docker，再用小型真实 manifest 运行本地构建，并检查：

```bash
docker history media-bundle:local
docker inspect media-bundle:local
```
