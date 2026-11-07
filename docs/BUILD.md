# 构建 OnePlus 13 Ubuntu

## GitHub Actions

打开仓库 Actions，运行 **OnePlus 13 persistent Ubuntu**，选择 main、desktop_profile=full，其余选项保持 false。默认使用 GitHub 托管的 ARM64 runner。ssc、kernel、resources、rootfs、mesa 分别构建，image 任务仅接受同一工作流同一提交的产物。

完成后下载 oneplus13-persistent-提交号 artifact 并解压。目录中包含正式 boot.img、临时安装 boot-ram.img、rootfs.squashfs、persistent-overlay.tar、tools、switch、文档、manifest.json 和 SHA256SUMS。先在解压目录执行以下校验。

~~~sh
sha256sum --check SHA256SUMS
~~~

full 构建启用 GNOME 首次设置。diagnostic 保留为开发用配置，仅生成 RAM 调试产物。

## 原生 ARM64 本地构建

使用干净的 Ubuntu ARM64 构建环境。依赖安装命令以 .github/workflows/oneplus13-source-image.yml 和 oneplus13-phone-ssc.yml 为准，包含编译器、Meson、Ninja、QMI/QRTR、protobuf、Wayland、DRM、GL、SquashFS、openssl 和 Python。rootfs 构建需要 root 权限及原生 ARM64 chroot。

构建顺序与工作流一致。

~~~sh
bash tools/oneplus13/source-image.sh kernel
# 运行 oneplus13-phone-ssc.yml 中的 SSC 和传感器构建步骤
# 运行 build-resources.sh，并保留 out/oneplus13-resources 中的包
bash tools/oneplus13/source-image.sh rootfs
bash tools/oneplus13/source-image.sh mesa
bash tools/oneplus13/source-image.sh assemble
bash tools/oneplus13/source-image.sh complete
bash tools/oneplus13/source-image.sh persistent
~~~

请将仓库放在 Linux 文件系统中。完整 GNOME 根文件系统包含区分大小写的路径，Windows 挂载目录不适合作为解包目录。使用同一源码提交的所有组件，按需清理可重建的 out 暂存目录后重建。

## 版本和来源

内核与工具固定版本位于 devices/oneplus13/source.json 和 kernel/source.json，Ubuntu base、包列表和 APT 快照位于 devices/oneplus13/ubuntu.json，Mesa 与音频、传感器来源分别位于 packaging 下的清单。third_party/qbootctl 保留经 Git blob 哈希核对的原始源码与 GPL-3.0 许可文本。

repo-cache-r280.json 是按哈希固定的源码、签名 APT 索引和软件包传输缓存。构建会重新编译内核、Mesa 与传感器组件，根文件系统也重新组装。缓存当前由适配来源仓库托管，其访问属于构建依赖。

设备数据位于 devices/oneplus13/qualified-data，需遵守 PROVENANCE.md 及供应商许可范围。更换设备数据时须更新清单并重新验证，特别是内存保留区、面板配置和传感器校准。

每次构建的会话值、AVB 密钥和镜像哈希可能不同。保留 manifest 中的源码提交和完整校验清单，以便对应到实际安装产物。对外分发同时提供源码入口、版本记录、LICENSE、NOTICE 和第三方要求的许可材料。详情见 [许可说明](LICENSES.md)。

## 上游分支同步

运行 **Sync original Pad upstream**。它获取 yzddmr6/onepluspad-2pro-mainline 的 main，仅快进更新本仓库 onepluspad-2pro-mainline。分支出现分叉时工作流失败，维护者需审查。需要采用上游改动时，在 main 上单独审查并适配。
