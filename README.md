# IELTS Codex

[![Tests](https://github.com/Miracle-0v0/ielts-codex-cli/actions/workflows/tests.yml/badge.svg)](https://github.com/Miracle-0v0/ielts-codex-cli/actions/workflows/tests.yml)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

一个受 Codex 终端交互启发的雅思背单词 CLI：斜杠命令、聚焦式单词卡片、即时反馈、间隔重复和本地学习统计。

它只使用 Python 标准库，不需要账号、联网或 API Key。

> [!NOTE]
> 本项目是独立的开源学习工具，并非 OpenAI 官方项目，也不隶属于或代表 OpenAI。

## 快速开始

要求 Python 3.10 或更高版本。

```bash
git clone https://github.com/Miracle-0v0/ielts-codex-cli.git
cd ielts-codex-cli
./ielts.py
```

也可以安装为 `ielts-codex` 命令：

```bash
python3 -m pip install .
ielts-codex
```

启动后会进入交互提示符：

```text
╭─ today ─────────────────────────────────────────╮
│ 今日进度  ░░░░░░░░░░░░░░░░░░░░  0/20           │
│ 待复习    0 个  ·  新词 72 个  ·  连续 0 天      │
╰─────────────────────────────────────────────────╯

› /learn 10 environment
```

## 命令

| 命令 | 作用 |
| --- | --- |
| `/learn [数量] [主题]` | 学习未见单词，默认 10 个 |
| `/review [数量] [主题]` | 复习今天到期的卡片 |
| `/quiz [数量] [主题]` | 中文到英文的拼写测验 |
| `/search <内容>` | 按英文、中文释义或近义词查询 |
| `/words [主题]` | 浏览词表和已学状态 |
| `/topics` | 查看各话题覆盖进度 |
| `/today` | 查看今日目标和建议路径 |
| `/stats` | 查看覆盖率、正确率、连续天数等统计 |
| `/goal <数量>` | 修改每日学习目标 |
| `/clear` | 清屏 |
| `/quit` | 退出 |

数量和主题的顺序可以互换：

```text
› /learn environment 8
› /review 15 教育
› /quiz 5 technology
```

在学习卡片中：

- `Enter`：显示答案
- `h`：查看挖空例句提示
- `s`：跳过，不改变进度
- `q`：结束当前一组
- `1` / `2` / `3` / `4`：按 Again / Hard / Good / Easy 评价记忆程度

直接输入一个单词也能查询：

```text
› ubiquitous
```

## 非交互模式

适合快捷命令或 shell 脚本：

```bash
python3 ielts.py stats
python3 ielts.py topics
python3 ielts.py search biodiversity
python3 ielts.py learn -n 5 -t environment
python3 ielts.py --no-color stats
```

## 词库与学习进度

内置词库包含 72 个雅思核心词，分为 9 个主题：

`culture`、`economy`、`education`、`environment`、`health`、`science`、`society`、`technology`、`work`。

每个词包含音标、词性、中英释义、双语例句、近义词、主题和建议分数段。

内置词条及例句由项目为本工具原创整理，没有复制商业词典或教材内容，并与项目代码一同按 MIT License 发布。

学习进度默认保存在：

```text
~/.ielts-codex/progress.json
```

每次评分后都会以“临时文件 + 原子替换”的方式立即保存，异常退出时也不容易损坏数据。可用 `IELTS_CODEX_HOME` 环境变量或 `--data-dir` 指定其他目录：

```bash
IELTS_CODEX_HOME=./my-progress python3 ielts.py
python3 ielts.py --data-dir ./my-progress stats
```

## 间隔重复规则

系统使用四档自评：

- `Again`：新词留在今日队列；遗忘的旧词次日重学
- `Hard`：短间隔复习，并略微降低难度系数
- `Good`：按 1 天、3 天和自适应间隔推进
- `Easy`：直接进入更长间隔

达到 21 天间隔或完成至少 5 次成功推进后，统计页会将单词计为“已掌握”。

## 测试

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

测试覆盖词库加载与检索、间隔调度、进度原子保存、损坏文件保护，以及完整的脚本化学习交互。

## 参与贡献

欢迎提交 Issue 和 Pull Request。开始前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

版本变化记录在 [CHANGELOG.md](CHANGELOG.md)。

## 开源许可

本项目采用 [MIT License](LICENSE)。
