# 安装、检查、故障与卸载

需要 Python 3.10+。从源码运行时不需要安装第三方 Python 运行库。

## 选择启动方式

| 方式 | 操作 | 适合情形 |
| --- | --- | --- |
| Windows 源码命令 | 在项目目录运行 `install.bat`，新开终端后用 `ielts` | 日常使用 |
| macOS/Linux 源码命令 | 运行 `./install.sh`，新开终端后用 `ielts` | 日常使用 |
| 便携启动 | Windows 用 `run.bat`；macOS/Linux 用 `./run.sh` | 不修改 PATH |
| Python 包 | 在项目目录运行 `python -m pip install .`，然后用 `ielts` | 已管理好自己的 Python 环境 |
| 直接源码运行 | `python ielts.py` | 明确使用某个 Python |

PowerShell 中当前目录命令写成 `.\install.bat`、`.\run.bat`。Windows 的 cmd 无法直接运行 `./run.sh`。

源码安装器只在用户目录创建启动命令，实际程序仍在当前项目目录。移动项目后重新运行安装器。默认 Windows 命令目录为 `%LOCALAPPDATA%\IELTS Codex\bin`；macOS/Linux 为 `~/.local/bin`。可用 `IELTS_CODEX_INSTALL_DIR` 指定其他位置，此时自行配置 PATH。

没有兼容 Python 时，启动器会询问是否通过 Astral uv 安装隔离的 Python 3.12。这一步需要联网和明确同意；文件保存在项目的 `.ielts-bootstrap` 目录，下次复用。它不会替换系统 Python。设置 `IELTS_CODEX_NO_AUTO_INSTALL=1` 可以禁用此下载；用 `IELTS_CODEX_PYTHON` 指定已经安装的解释器。

## 安装后检查

在新终端运行：

```text
ielts --version
ielts
```

进入界面后运行 `/update status` 查看本地版本，运行 `/study 20` 开始学习。`status` 不联网。本文对应 `1.0.0`；三平台实测结果与发布流程见 [1.0 说明](1.0_PLAN.md)。取得不同分支或安装包时，以 `--version` 的实际结果为准。

可用独立数据目录试用，避免与日常进度混用：

```text
ielts --data-dir ./trial-progress
```

需要备份或转移时先退出程序，再复制整个数据目录。默认是用户目录中的 `.ielts-codex`，并不在项目源码里。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| 找不到 `ielts` | 新开终端；确认安装器显示的命令目录在 PATH。也可直接运行项目内启动器。 |
| Windows Python 弹出商店或版本过旧 | 安装 Python 3.10+，或允许启动器使用隔离 Python；也可设置 `IELTS_CODEX_PYTHON`。 |
| 中文或像素显示异常 | 使用支持 Unicode 的终端与字体；游戏可设置 `IELTS_CODEX_GAME_TURN_BASED=1` 使用文字模式。核心学习不依赖像素显示。 |
| 提示进度写入冲突 | 退出较旧实例，重新打开读取最新进度；冲突的未保存回答需重新作答。无需删除锁文件。 |
| 主进度无法读取 | 运行 `ielts backups`，再用 `ielts restore <备份名>`；自定义目录加相同的 `--data-dir`。 |
| 程序更新拒绝修改源码 | 检查是否官方 main、是否有本地改动；先保存自己的修改，再自行决定如何同步。不要为了更新删除学习数据。 |
| 候选版提示比最新稳定版新 | 这是正常比较结果；更新器不会将候选版降回较旧稳定版。正式同版本发布后可升级。 |
| 词典或宠物 API 无法联网 | 继续使用本地词库、已有词典参考和默认宠物；核心学习无联网要求。 |

## 卸载程序与保留进度

**源码命令安装：** 退出应用，删除安装器创建的命令即可。Windows 默认目录中是 `ielts.cmd` 和 `ielts.ps1`；macOS/Linux 默认是 `~/.local/bin/ielts`。自定义安装应使用当时指定的位置。仅删除确认为本项目的命令文件，避免影响其他程序。

Windows 可在用户环境变量中移除本项目专用命令目录。Unix 安装器可能向 `~/.bashrc`、`~/.zshrc` 或 `~/.profile` 加入 `~/.local/bin`；其他程序也可能使用该目录，通常保留这条 PATH 设置即可。

**Python 包安装：** 使用安装时的解释器运行 `python -m pip uninstall ielts-codex`。源码启动器和包安装可能同时存在；命令仍可用时，检查 PATH 中是否还有另一份。

不再需要源码时，可以另外删除克隆目录及其中的 `.ielts-bootstrap`。这些操作不会自动删除默认学习数据。只有明确不再需要进度时，才自行删除 `~/.ielts-codex` 或曾指定的自定义数据目录；建议先保留一份完整副本。
