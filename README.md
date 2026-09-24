# IELTS Codex

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

IELTS Codex 是面向中文学习者、离线优先的雅思词汇与表达训练 CLI。在终端里认识单词、检查拼写、练习语境，并在隔天复习中验证记忆。认义、拼写和语境分别安排复习，分别显示表现。

核心学习只依赖 Python 标准库，无账号，无 API Key。保留斜杠命令和像素游戏；联网更新与自定义宠物均为可选功能。

**[1.0.0 正式版](https://github.com/Miracle-0v0/ielts-codex-cli/releases/tag/v1.0.0) 已发布。**Windows、Ubuntu 和 macOS 已完成真实终端实测，Python 3.10 已实际运行。版本范围、验证依据与发布流程见 [1.0 说明](docs/1.0_PLAN.md)。

它辅助词汇与表达训练，不覆盖完整雅思模考，不把词汇记录换算成官方分数，也不保证提分。

> [!NOTE]
> 这是独立开源学习工具，不是 OpenAI 官方产品，与 OpenAI 无隶属或背书关系。

## 安装与启动

需要 Python 3.10+。没有可用 Python 时，启动器会征得同意后通过 Astral uv 下载隔离的 Python 3.12；之后核心学习可离线运行。

**Windows：**

```bat
git clone https://github.com/Miracle-0v0/ielts-codex-cli.git
cd ielts-codex-cli
install.bat
```

**Ubuntu / macOS：**

```bash
git clone https://github.com/Miracle-0v0/ielts-codex-cli.git
cd ielts-codex-cli
./install.sh
```

以上安装方式随所取得的源码版本运行。安装后新开终端，运行 `ielts --version` 确认，再运行 `ielts`。也可以用 `run.bat` 或 `./run.sh` 便携启动。源码安装器指向项目目录，请保留该目录。更多方式、故障处理与卸载见 [安装说明](docs/INSTALLATION.md)。

## 第一次和每天怎么学

```text
/study 20
```

首次设置每日时间、主要薄弱项和内容包，之后即可直接开始学习。时间可选 5～60 分钟，是任务预算，不是倒计时。系统先处理到期任务，再安排新词与未验证能力；积压时减少新词，连续至少三天没有学习记录时提供较短的恢复计划。

中途输入 `q` 返回，下次 `/study` 继续保存的计划。`/study new 20` 明确重新安排未答任务，已完成记录保留。每次回答与计划位置一起保存。Again 会在本组稍后重练，每词每组最多三次，并有整组次数上限。

也可以独立训练：

| 命令 | 用途 |
| --- | --- |
| `/review 10` | 认义到期复习 |
| `/learn 5` | 学习新词 |
| `/quiz 5` | 中译英拼写 |
| `/context 5` | 固定选项的词形和搭配练习 |
| `/mistakes` | 查看待练错项及相关能力的最近表现 |
| `/mistakes practice spelling` | 重点练习拼写薄弱项 |
| `/today`、`/stats` | 今日进度、分题型表现和稳定复习 |
| `/game` | 像素冒险，游戏表现独立记录 |

输入 `/` 打开命令列表。拼写只忽略大小写和首尾空白，合法变体由词条的 `accepted_answers` 明确列出。

## 加入自己遇到的词

```text
/add commute
/import "reading.csv" reading
/decks
/decks reading
/decks all
```

`/add` 先收进待补全词条，不自动生成释义或标准答案。CSV/JSON 导入会先展示新增、补全、重复和错误，再确认写入。不完整条目不参与训练；通过原 ID 导入词性和中文义项即可补全。

词条使用稳定 ID，同一拼写可以有不同义项。`/import undo` 撤销最近一次导入，也可指定批次；已有学习记录保留。`/decks` 选择后续学习内容包，已有每日计划保持原安排，需重排时用 `/study new`。

使用 [CSV 示例](examples/vocabulary.csv) 或 [JSON 示例](examples/vocabulary.json) 起步，完整格式见 [个人词库](docs/VOCABULARY.md)。

## 怎样理解进步

“已接触”“这次答对”“延迟复习通过”表示不同事情。认义来自自评，拼写和语境按明确答案判定；提示后完成、本组重练和游戏记录分别保存。

“稳定复习”按能力统计：连续至少三次跨日、无提示的 Good/Easy，最近一次距上次记录练习至少七天；第一次详细记录只建立起点。错误、Hard 或提示会中断该能力的连续通过，本组重练不增加通过次数。同日重复和刚看完答案后的重练不会快速拉长间隔。

认词通过不会把拼写或语境自动标成通过。语境记录仅表示这些固定选项题的表现，不代表自由造句或掌握一个词的全部用法。这些都是项目学习规则，不是雅思评分或记忆能力测量。详见 [学习记录与恢复](docs/LEARNING_DATA.md)。

## 进度、备份和更新

数据默认保存在 `~/.ielts-codex`，可用 `--data-dir` 或 `IELTS_CODEX_HOME` 改位置。两个实例发生写入冲突时会拒绝覆盖。旧进度升级到格式 3 前，会在首次成功保存前留下完整原始备份，无需删除重来。

- `/backup`、`/backups`、`/restore <备份名>`：创建、查看和恢复进度备份。
- `/update` 或 `/update status`：离线查看程序、内置内容和外部词典版本。
- `/update program`：检查并升级到正式稳定版；不会安装预发布版本或降级。
- `/update dictionary`：预览 OEWN 外部参考变化，确认后保存。
- `/update dictionary rollback`：确认后恢复最近一次更新前的参考。

外部参考单独显示，不覆盖教学释义、个人词条或训练答案。联网失败不影响本地学习。完整备份请复制整个数据目录；卸载程序不会自动删除学习记录。

## 内容与后续范围

内置 72 个词条、9 个话题，以及 24 道原创语境题（13 道词形、11 道搭配）。词条分层“核心／进阶／拓展”是项目编辑标签，不对应官方 Band。题目已做自动检查与代理阅读检查，不声称完成人工或外部审校。

后续将逐步增加经过检查的词条，个人词库可用于持续扩展自己的学习内容。自由造句、完整作文反馈、语音评分和多设备同步留待后续。

代码与项目内置内容采用 MIT 许可；词库不是剑桥官方词表。OEWN 参考保留原有许可，署名与来源见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。遗忘曲线是概念性展示，不代表个人记忆效果测量。

## 演示与贡献

![IELTS Codex 1.0 每日学习、拼写、语境和个人词库演示](docs/demo.gif)

上方 GIF 重新录自 1.0 的真实终端交互，使用独立的临时演示数据：今日学习 → 退出后继续 → 拼写反馈与错词复习 → 语境训练 → 导入个人词库 → 分能力统计。录制方式见 [贡献指南](CONTRIBUTING.md#refresh-the-readme-demo)。

参见 [文档导航](docs/README.md)、[完整功能参考](docs/REFERENCE.md)、[贡献指南](CONTRIBUTING.md) 和 [更新历史](CHANGELOG.md)。
