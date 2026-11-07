# 启动与运行机制

## 存储

Android 使用槽位 A，Ubuntu 的 boot.img 放在 boot_b。Ubuntu 根文件系统位于 linux_rootfs ext4 分区，公共媒体位于 shared_media ext4 分区。参考 1 TB 布局给 Android userdata 分配 448 GiB、Ubuntu 根分区分配 64 GiB，其余空间作为共享媒体。Android A 的系统启动链保留，首次调整 userdata 后通过原厂恢复环境重新格式化。

## Ubuntu 启动

boot.img 的 initramfs 在 RAM 写保护逻辑启动前识别 oneplus13.persistent=1，挂载 linux_rootfs，核对内核版本并将显示载荷交接至新根。systemd 启动前生成当前启动的硬件服务。硬件服务依次校验绑定、等待温度就绪、初始化 CPU、ADSP、触控及 DSI/GPU。外设服务继续初始化 Wi-Fi 和 SSC。

持久化模式使用独立会话标记。临时 RAM 模式的诊断超时与 UFS 写保护由 RAM 会话条件限制。正式镜像默认关闭 USB 诊断服务，SSH 使用用户首次设置的账号认证。

## 双向切换

Android 的 KernelSU 模块核对启动完成、A 槽位、Boot Control HAL 版本、快照状态及 B 镜像哈希，然后选 B 并正常重启。Ubuntu 的菜单通过 polkit 认证调用 op13-switch，校验 B 启动镜像和 qbootctl，选 A 后通过 systemd 正常重启。Linux 成功进入硬件就绪的桌面或充电模式后，服务标记 B 为启动成功。

## 接电关机

bootloader 开启 off-mode-charge 后，USB_CHARGER 触发的启动携带 androidboot.mode=charger。persistent-launch 从移动后的 /newroot/proc/cmdline 读取模式，进入 oneplus13-charger.target。它启动必要硬件、电池监控与电源键处理，保持屏幕关闭。供电连续消失三秒后请求关机，电源键持续按住两秒后重启进入正常桌面。启动阶段留有十五秒读数宽限，未知读数不会当作断电。

## 首次设置与共享盘

源构建 rootfs 没有普通用户，machine-id 为空，SSH 主机密钥在首次启动生成，GDM 启动 GNOME Initial Setup。第一账号建立后，定时服务为 UID 1000 设置公共媒体 ACL。Android 公共媒体通过挂载替换接入共享盘，Android 和 obb 子目录保留在加密 userdata 内。
