# 许可与来源

项目以 GPL-2.0-only 发布原始集成代码。各文件的原始版权、SPDX 和独立许可仍适用。删除或更新用户文档时保留 LICENSE、NOTICE 和第三方材料。原始项目可在 onepluspad-2pro-mainline 分支追溯。

| 组成 | 来源记录与许可 |
| --- | --- |
| 原 Pad 集成项目 | yzddmr6/onepluspad-2pro-mainline，保留上游历史、版权及许可证 |
| Linux | devices/oneplus13/source.json 固定源码提交，devices/oneplus13/kernel-patches 和驱动源码提供修改，内核 COPYING / LICENSES / SPDX 适用 |
| 社区板级 DTS | devices/oneplus13/source.json 的 board_reference，原 BSD-3-Clause 版权保留 |
| Mesa | packaging/mesa/source.json 固定版本，原 MIT 等逐文件许可适用 |
| AudioReach | packaging/audio/source.json，保留原 BSD 许可 |
| SSC / 传感器 | packaging/sensors 及构建工具固定来源，保留 libssc、hexagonrpc 和 iio-sensor-proxy 的许可与构建证据 |
| qbootctl | third_party/qbootctl/source.json 固定提交，独立程序使用 GPL-3.0-or-later，原 GPL-3.0 文本及各文件声明保留 |
| AOSP 工具 | kernel/source.json 与 packaging/build-inputs，保留 mkbootimg、AVB 和安装转换器的原 Apache-2.0 / 文件许可 |
| Ubuntu / BusyBox | Ubuntu 版本及包快照清单位于 devices/oneplus13，软件包各自的版权及源码许可适用 |
| 固件与参考数据 | packaging/firmware-manifest.json、packaging/boot-reference/source.json、devices/oneplus13/qualified-data/PROVENANCE.md，供应商条款单独适用 |

qbootctl 通过独立可执行程序被调用，其源码和许可随 persistent-overlay 一同安装。项目的 GPL-2.0-only 声明不覆盖其 GPL-3.0-or-later 源码。

内核和其他受 copyleft 约束的二进制分发，应同时提供匹配的完整对应源码、补丁、配置及构建脚本。Actions 保留同一提交的源码版本、内核材料和构建清单。Ubuntu 软件包的版权位于系统 /usr/share/doc，各包源代码可按固定的 Ubuntu 快照、源包名与版本获取，获取源码时启用对应 deb-src。重新分发者应保留这些材料并满足各组件的源码提供义务。

设备拥有者对所提供数据的公开授权是数据来源记录，不能替代第三方供应商的分发许可。Qualcomm、OnePlus、Awinic 和 ROCKNIX 来源的固件、签名与设备配置按原条款处理，GPL 不授予额外分发权。现存来源记录中尚缺独立供应商授权文本的部分仍保留明确的专有数据标记，后续分发需依据相应权利人的条款核实。传感器校准与保留内存布局还具有设备适用范围。

完整第三方归属见仓库根目录 NOTICE。源码分支仅包含经审查的构建输入，个人系统快照、账号、Wi-Fi 凭据、设备唯一标识和签名私钥应保存在本地。
