# CommandGuard · 命令名单管理器

<img src="icon_preview.png" width="96" alt="icon">

> 图形化管理 **CodeBuddy 系产品**（WorkBuddy 桌面版 / CodeBuddy 命令行 / CodeBuddy CN IDE）的
> 命令「放行 / 询问 / 拦截」名单。三栏并排、实时读取；自动检测配置位置，也可手动指定路径保底。

> ⚠️ **非官方工具**：本项目与腾讯及其 CodeBuddy / WorkBuddy 产品无隶属关系，仅通过读写本地配置文件实现互操作。

## 它管理什么

| 栏 | 对象 | 默认配置位置 | 可管理内容 |
|---|---|---|---|
| ① | WorkBuddy 桌面版 | `~/.workbuddy/settings.json` | 沙盒命令规则（允许/询问/拒绝）+ Bash 权限 |
| ② | CodeBuddy 命令行 | `~/.codebuddy/settings.json` | Bash 权限（`Bash(rm:*)` 这类规则） |
| ③ | CodeBuddy CN IDE | `%APPDATA%\<CodeBuddy 目录>\User\globalStorage\state.vscdb` → 库键 `CodeBuddy.settings` | 命令黑名单（正则）/ 安全检查类别 / 运行模式 |

> **为什么 IDE 要直连数据库？** CodeBuddy CN IDE **不读取** `User\settings.json` 里那些 `codingcopilot.*` 设置，
> 真正生效的是它自己数据库里的 `CodeBuddy.settings`（明文 JSON），且**只在 IDE 启动时读取**。
> 所以本工具的 ③ 栏直接读写该数据库，这是实测唯一有效的方式。

## 功能

- 三栏并排、每 5 秒自动刷新（**差值检测**，内容没变不重绘、不打断浏览）
- 允许 / 询问 / 拒绝 三态规则；命令前缀、正则黑名单、13 类安全检查开关、运行模式
- 每个列表**搜索过滤**；**右键快捷操作**（改动作 / 删除 / 「复制到…」其他栏）
- 跨栏复制**自动转换格式**：`命令规则 ↔ Bash(...:*)`，重复项自动跳过
- **自动检测配置位置**，检测不到的可在「设置…」里**手动指定（保底）**
- 每次保存**自动备份** + 写入前 JSON 自校验；只改名单相关字段，其余内容原样保留
- 窗口大小位置记忆；`--selftest` 离线自检；`--smoke` 界面冒烟

## 下载使用

1. 到 **Releases** 页面下载 `CommandGuard.exe`（单文件，免 Python；Windows 可能提示"未知发布者"，选"仍要运行"）。
2. 首次启动自动检测三个配置位置；显示"未检测到"的，点「设置…」手动指定对应文件即可。
3. 在列表里增删改规则（或右键快捷操作），完成后点「保存全部」。生效方式：

| 对象 | 生效方式 |
|---|---|
| ① 桌面版 | **重启 WorkBuddy** |
| ② 命令行 | **下次启动新会话** |
| ③ IDE | 先完全退出 IDE → 保存 → **重新打开 IDE**（它只在启动时读取设置；工具会提醒） |

## 从源码运行 / 构建

- 环境：Windows + Python 3.10+（**仅标准库**，无第三方依赖；需要 tkinter，官方 Python 自带）
- 直接运行：`python command_guard.py`
- 构建单文件 EXE：安装 PyInstaller（`pip install pyinstaller`）后双击 `build.bat`
- 离线自检：`python command_guard.py --selftest`（结果写在 `selftest-out/`，不触碰真实配置）

## 文件说明

```
command_guard.py   主程序（tkinter 三栏界面 + 名单读写 + 自检）
gen_icon.py        生成 icon.ico 与预览图（纯标准库）
build.bat          一键打包单文件 EXE
LICENSE            GNU GPLv3
```

## License

本项目使用 **GNU General Public License v3.0（GPLv3）** 许可 — 详见 [LICENSE](LICENSE)。

Copyright (C) 2026 GuDogShu

> 简单说：你可以自由使用、修改、分发；但**衍生作品也必须以 GPLv3 开源**，且需保留版权与许可声明。

---

## English Summary

CommandGuard is an unofficial GUI tool to manage command allow/ask/deny lists for the CodeBuddy
family (WorkBuddy desktop, CodeBuddy CLI, CodeBuddy CN IDE). It auto-detects config locations
(with manual override fallback), edits only list-related fields with automatic backups, and
supports per-list search, right-click quick actions and cross-pane rule copying.
Python 3.10+ / tkinter; single-file EXE available in Releases.
Licensed under the **GNU General Public License v3.0 (GPLv3)**.
