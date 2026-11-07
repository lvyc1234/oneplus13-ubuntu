# OnePlus 13 Ubuntu

一加 13（dodge / SM8750）的 Ubuntu 持久化双系统适配。当前参考系统为 Ubuntu 26.04.1 LTS、GNOME Wayland、Linux 7.2 和项目构建的 Mesa 26.2.3。安装后直接从手机切换 Android 与 Ubuntu。

当前实机基线是 PJZ110、24 GB 内存、1 TB 存储、ColorOS C.93。设备树含该内存规格的保留内存布局，安装器也校验该存储几何。其他型号、内存和存储版本需要独立适配及验证。

## 当前状态

2026 年 11 月 7 日，现有安装完成了以下实机验收。

| 功能 | 当前结果 |
| --- | --- |
| 持久化启动 | ext4 根分区，重启保留系统和用户数据 |
| 系统切换 | Android A 经 KernelSU 模块进入 Ubuntu B，Ubuntu 应用菜单可重启到 Android |
| 屏幕与触控 | 原生 DSI 屏幕和触控驱动 |
| GPU 桌面 | Adreno 830v1 硬件渲染，GNOME 动画及旋转恢复流畅 |
| Wi-Fi | 自动连接，固定每连接的稳定 MAC 身份 |
| 自动旋转 | SSC 加速度计驱动横竖屏切换 |
| 充电 | 开机前已接电、电脑 USB、原装充电器及拔插状态已验收 |
| 电源键 | 短按锁屏，再按唤醒并保留认证 |
| 关机 | 无线关机保持关闭，接电关机进入黑屏充电模式并保持桌面关闭 |
| 共享存储 | Android 文件管理器与 Ubuntu 共享公共媒体目录 |

接电关机利用 bootloader 的 off-mode-charge 与最小 Linux 充电 target。拔线后关机，长按电源恢复正常启动。深度挂起、续航、摄像头、GNSS、NFC、视频硬件解码、通话和移动数据尚未达到本项目的稳定桌面验收标准。完整范围见 [当前状态](docs/oneplus13/STATUS.md) 与 [限制](docs/oneplus13/TECHNICAL-DEBT.md)。

## 构建与安装

在 Actions 运行 **OnePlus 13 persistent Ubuntu**，选择 main 和 full。构建从同一提交生成内核、模块、GNOME 根文件系统、Mesa、传感器组件及持久化安装包。产物名称为 oneplus13-persistent-提交号，包含 boot.img、boot-ram.img、rootfs.squashfs、persistent-overlay.tar、安装工具和校验清单。

全新系统由 GNOME 首次设置创建账号及密码，镜像内普通用户数量为零。当前手机的用户、网络配置和个人数据不会作为构建输入。构建产物仍需按安装文档完成设备检查与实机验收。

- [构建说明](docs/BUILD.md)
- [安装、重装与恢复](docs/INSTALL.md)
- [启动和切换机制](docs/ARCHITECTURE.md)
- [协议与第三方来源](docs/LICENSES.md)

首次分区会清除 Android 用户数据。现有双系统可选择仅重装 Ubuntu 根分区，Ubuntu 账号及文件会被重置。操作前按安装文档备份 GPT、启动分区和用户数据。

## 分支

- main 保存 OnePlus 13 当前稳定方案及可构建安装流程。
- onepluspad-2pro-mainline 保留原项目 main。Actions 的 **Sync original Pad upstream** 将原项目后续 main 快进同步到该分支。

上游同步由手动工作流触发。出现分叉时工作流报错，维护者需审查后处理。OnePlus 13 的 main 独立维护。

## 来源与许可

本项目衍生于 [yzddmr6/onepluspad-2pro-mainline](https://github.com/yzddmr6/onepluspad-2pro-mainline)。原作者及第三方文件版权声明保留。项目集成代码遵循 [GPL-2.0-only](LICENSE)，具体文件以各自声明为准。单独构建的 qbootctl 遵循 GPL-3.0-or-later。固件及校准数据遵循各自供应商条款，项目 GPL 不授予其额外使用或分发权。详情见 [NOTICE](NOTICE) 和 [许可说明](docs/LICENSES.md)。
