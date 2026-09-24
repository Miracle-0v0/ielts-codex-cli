# 内容包与个人词库

本文适用于本地候选版 `1.0.0rc1`。

内置内容从 72 个词条、9 个话题开始。1.0 候选版优先建立可扩展的学习路径，没有将内容数量虚增为 300～500，也没有声称完成外部人工审校。

内置词条按“核心／进阶／拓展”展示项目编辑分层。这些标签与雅思官方分数没有对应关系。程序版本、内置内容版本和可选 OEWN 参考版本分开显示。

## 导入自己的词

```text
/import "reading.csv" reading
/import "my vocabulary.json"
/decks
/decks reading
/decks all
```

文件路径包含空格时加引号。导入先检查并显示新增、补全、重复项与错误，再询问是否写入。任何非法行都会阻止整批导入；重复义项跳过。不会部分写入后才报告剩余错误。

可从 [CSV 示例](../examples/vocabulary.csv) 或 [JSON 示例](../examples/vocabulary.json) 开始编辑；它们不会自动导入。

可使用 UTF-8 或带 BOM 的 UTF-8 CSV。CSV 至少需要 `word` 表头；可训练条目至少有 `word`、`part_of_speech`、`meaning_zh`：

```csv
word,part_of_speech,meaning_zh,definition_en,example,example_zh,topic,collocations
commute,v.,通勤,Travel regularly between home and work.,She commutes by train.,她乘火车通勤。,work,commute to work|commute by train
```

JSON 可为条目数组，也可带内容包名：

```json
{
  "deck": "reading",
  "words": [
    {
      "word": "commute",
      "part_of_speech": "v.",
      "meaning_zh": "通勤",
      "collocations": ["commute to work", "commute by train"],
      "source": "个人阅读笔记"
    }
  ]
}
```

没有提供的英文释义或例句保持空白。导入成功表示文件符合规则，不表示释义已经由项目核实；个人内容需要自己检查准确性。

## 先收词，再补全

```text
/add commute
```

只有单词时，条目进入 `pending`（待补全）状态，不参与学习选题和判分。导入只有 `word` 的行也会如此处理。包含部分教学字段却缺少词性或中文释义时会报告错误；确实要暂存时显式设 `status=pending`。

补全已有待补全词时，在导入文件的 `id` 字段填入界面显示的该词 ID，再提供词性、中文释义等内容。这样保留同一个词条身份；省略的内容包、笔记、来源和使用说明保留原值，显式给出时才更新。不要把补全误当成新建另一条记录；若补全结果与另一条已有义项重复，会报告错误。

## 字段说明

| 字段 | 含义与格式 |
| --- | --- |
| `word` | 英文单词或表达；必需 |
| `id` | 稳定身份；首次导入可省略，补全已有词条时使用原 ID |
| `deck` | 个人内容包名称；可由文件外层或命令指定 |
| `part_of_speech`、`meaning_zh` | 可训练条目必需的词性和中文义项 |
| `status` | `ready` 或 `pending` |
| `level` | `core`、`advanced`、`extension`；仅为编辑分层 |
| `phonetic`、`definition_en` | 音标与英文释义，可省略 |
| `example`、`example_zh` | 例句及中文译文，可省略 |
| `topic` | 话题分类 |
| `synonyms`、`accepted_answers` | 近义词与明确允许的拼写答案 |
| `collocations`、`forms`、`common_errors` | 搭配、词形和常见错误 |
| `usage`、`notes`、`source` | 使用场景、笔记和来源说明 |
| `definition_source`、`definition_license`、`definition_source_url` | 释义来源及许可；外部来源应保留原有署名和条款 |

列表字段在 JSON 中使用数组，CSV 中用 `|` 分隔。`accepted_answers` 不会由同义词推导：拼写题接受目标词以及明确列出的变体，仅忽略大小写和首尾空白。

`core` 内容包和 `core-` ID 前缀供内置内容使用，`all` 保留为全部内容包的选择值。旧 `band` 字段仍可作为兼容输入，界面不再用它暗示单词对应官方分数。

## 身份、重复项与撤销

同一个拼写可以按不同词性或中文义项分别保存。省略 ID 时，系统根据内容包、拼写、词性和中文义项生成可重复得到的个人 ID；完全相同的义项会作为重复项跳过，即使写在不同内容包中。

```text
/import undo
/import undo <批次ID>
```

不指定批次时撤销最近一次仍有效的导入。撤销恢复该批次之前的词条内容，已有卡片与逐次学习记录保留。若随后又修改了同一条目，旧批次撤销会报告冲突，不会覆盖后续修改。用同一身份重新导入时可接续原进度。

个人词条与导入记录保存在 `progress.json` 内，随进度一起备份和恢复。删除或撤销词条不会等同于抹掉历史表现。内容包选择影响后续学习与新建计划；已有每日计划保持原安排，需重排时使用 `/study new`。

## 语境题的范围

候选版内置 24 道原创固定选项题，覆盖现有 9 个话题：13 道词形题、11 道搭配题。每题保存目标词 ID、题干、选项、正确答案、中文解释和提示；没有从任意例句自动挖空。

使用 `/context 5` 练习。可输入选项编号或对应选项的完整文本；文本只忽略大小写和首尾空白。判分基于题目给定的答案，不评价开放造句。遇到语境歧义或不妥解释，应修改题目，不能用字符串相似度放宽标准答案。

这些题目是项目原创内容，不是官方雅思题目，也未声称完成人工或外部审校。语境通过或稳定复习仅针对这些固定选项题，不代表能自由造句或已经掌握目标词的全部用法。部分题目练习的是句中搭配，不能据此推断目标词的其他使用能力。个人词条导入暂不导入或自动生成语境题；没有题目的词会显示语境尚未验证，而非已经通过。

## 外部词典参考

`/update dictionary` 获取 OEWN 参考释义并预览变化，确认后保存；`/update dictionary rollback` 预览并恢复最近一次更新前的参考。只保留这一层回退；首次安装后的回退会移除外部参考。项目与个人词条的教学释义、例句、义项和题目不被覆盖。词典版本更新不等于教学内容完成审校。

保留外部参考的来源、许可和版本。OEWN 的完整许可说明见 [第三方声明](../THIRD_PARTY_NOTICES.md)。
