# IELTS Codex

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

IELTS Codex 是一个在终端里使用的雅思词汇训练器，借鉴了 Codex 的交互风格：斜杠命令、词卡、拼写测验、间隔重复，把背单词做成一件有点酷的事。

它只依赖 Python 标准库，零运行时依赖；平时启动完全离线，不需要账号、API Key 或联网。当前学习界面以中文为主。

> [!NOTE]
> 这是一个独立开源学习工具，不是 OpenAI 官方产品，与 OpenAI 无隶属或背书关系。

## Demo

![IELTS Codex terminal demo](docs/demo.gif)

## 亮点功能

- 斜杠命令面板：输入 `/` 呼出命令列表，方向键选择，Tab 补全
- 三合一学习闭环：学新词、到期复习、中译英拼写测验
- 间隔重复：用 Again / Hard / Good / Easy 四级评分，自动安排复习
- 艾宾浩斯遗忘曲线：学习完直接在终端里看到记忆衰减趋势
- 像素风口袋冒险小游戏：在迷雾地图里按顺序“捕获”单词小怪兽
- 本地合成 8-bit BGM：游戏配乐纯本地生成，没有第三方素材
- 图片生成像素宠物：可选接入自己的视觉 API，把照片变成陪练伙伴
- 本地进度存储：原子写入 JSON，学习记录不怕中途退出
- 纯离线启动：无账号、无 API Key、无依赖，打开就能用
- 手动更新：`/update` 可刷新 WordNet 英文释义，并安全升级到新版

## 快速上手

需要 Python 3.10+。如果系统里没有，启动器会提示通过 Astral uv 安装一个隔离的 Python 3.12，不影响系统 Python。

**Windows：**

```bat
git clone https://github.com/Miracle-0v0/ielts-codex-cli.git
cd ielts-codex-cli
install.bat
ielts
```

**Ubuntu / macOS：**

```bash
git clone https://github.com/Miracle-0v0/ielts-codex-cli.git
cd ielts-codex-cli
./install.sh
ielts
```

不想安装命令的话，也可以直接运行 `run.bat`（Windows）或 `./run.sh`（Linux / macOS）便携启动。

进入界面后输入 `/` 打开命令面板，常用的有：

| 命令 | 作用 |
| --- | --- |
| `/learn` | 学习新词 |
| `/review` | 复习到期卡片 |
| `/quiz` | 中译英拼写测验 |
| `/game` | 像素冒险小游戏 |
| `/search <单词>` | 按英文、中文或同义词搜索 |
| `/stats` | 查看学习统计 |

## 词库来源

内置词库目前有 72 个词条，覆盖 9 个雅思常见话题，由项目作者基于雅思词汇知识人工编写整理，包含音标、英文释义、中文释义、双语例句、同义词、话题和 band 值，随项目以 MIT 协议发布。它不是剑桥官方词表。

此外，`/update` 可以手动从 Open English WordNet（OEWN）同步最新的英文释义；中文释义、例句等人工整理内容不会被覆盖。OEWN 内容遵循 CC BY 4.0 及 Princeton WordNet 许可，具体署名、修改说明和链接见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 证书 / 声明

- 项目代码与内置词库：MIT License
- 第三方词源（OEWN）：见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
- 本工具是独立开源项目，非 OpenAI 产品，与 OpenAI 无隶属或背书关系
- 遗忘曲线等展示为概念性估计，不构成对个人记忆效果的测量或承诺

欢迎提交 Issue 和 PR，贡献指南见 [CONTRIBUTING.md](CONTRIBUTING.md)，更新历史见 [CHANGELOG.md](CHANGELOG.md)。
