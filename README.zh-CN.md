# skills-lint

一个检查 agent skill 会不会被选中的小工具。

Skill 失效的时候没有任何提示。你写好了、放进目录，模型要么在该用的时候用了，要么永远
不用。没有报错，没有日志，也搜不到原因。通常要过几周才发现：这件事 agent 一直是在手搓。

多数情况问题不在正文，而在 description。运行时拿它去匹配用户请求，一份只罗列"这个
skill 包含什么"的描述，等于没给匹配器任何抓手。

skills-lint 按运行时的视角把 skill 读一遍，指出信号丢在哪。

只用 Python 标准库，3.8 以上。任何含 `SKILL.md` 的目录都能跑，Claude Code、Codex，以及
沿用同一约定的其他工具都适用。

```bash
python skills_lint.py ~/.claude/skills
```

```
skills-lint 1.0.0
==================================================================
pdf-tools                 99  [####################]
   [INFO] SL017 8 file(s) under scripts/ are never mentioned in SKILL.md
music-search              88  [##################..]
   [FAIL] SL010 description never says when to use the skill
          Add an explicit trigger clause, for example 'Use when the
          user asks to ...'. This is the single highest-impact fix
          for a skill that never fires.
news-digest              100  [####################]
   [ OK ] no findings
------------------------------------------------------------------
8 skill(s), average score 94, 3 failure(s), 2 warning(s)

most impactful fixes first:
  music-search         SL010: description never says when to use the skill
```

上面这次是对八个在用的 skill 跑的，其中三个从来没写过触发条件。

## 抓的是什么问题

同一个 skill，两种写法：

```yaml
# 永远不会被选中。这是产品目录，不是触发条件。
description: PDF manipulation toolkit for extracting text and tables,
  creating PDFs, merging and splitting documents.
```

```yaml
# 会被选中。说清了在什么场景用，用的是用户真会打出来的词。
description: Manipulate PDF files. Use when the user asks to extract text
  or tables from a PDF, merge or split documents, fill in a form, or
  generate a PDF programmatically.
```

两份描述都没写错，但只有第二份给了匹配器东西可匹配。

## 规则

`--list-rules` 打印完整索引。

| 规则 | 等级 | 抓什么 |
|---|---|---|
| `SL001` | fail | `SKILL.md` 缺失，或写成了 `skill.md` / `Skill.md` |
| `SL002` | fail | 没有 YAML frontmatter |
| `SL003` | fail | 没有 `name` |
| `SL004` | warn | `name` 与目录名不一致 |
| `SL005` | warn | `name` 不是小写连字符格式 |
| `SL006` | warn | `name` 太泛（`helper`、`utils`、`tools`…） |
| `SL007` | fail | 没有 `description` |
| `SL008` | warn | `description` 短于 40 字符 |
| `SL009` | warn | `description` 超过 1024 字符 |
| `SL010` | **fail** | **`description` 没说清什么时候用** |
| `SL011` | fail | `description` 只是重复了名字 |
| `SL012` | warn | 正文几乎没有内容 |
| `SL013` | info | 正文长到该拆进 `references/` |
| `SL014` | fail | 引用的路径不存在 |
| `SL015` | warn | 危险命令（`rm -rf /`、`curl \| sh`、force push…） |
| `SL016` | fail | 疑似硬编码密钥 |
| `SL017` | info | `scripts/` 下有文件但正文从没提过 |

触发词识别支持中英文：`use when`、`when the user`、`when claude`、`当用户…时`、`…时使用`、
`适用于`、`用于` 等。要加新写法，改一行正则就行。

## 评分

满分 100，每个 fail 扣 12，warn 扣 5，info 扣 1。这个分数用来快速定位问题，不是评级。
100 分只表示没有机械性的问题会阻止这个 skill 被选中，不代表指令写得好。指令质量只能靠
人判断，工具不管这块。

## 放进 CI

```bash
python skills_lint.py ./skills --min-score 70
python skills_lint.py ./skills --fail-level warn
python skills_lint.py ./skills --json > skills-report.json
```

退出码：`0` 干净，`1` 有达到 `--fail-level` 的问题，`2` 用法错误。

默认 `--fail-level fail`，只拦会导致选不中的问题。想更严格就换成 `warn`。

`--json` 输出每个 skill 的规则号、行号和提示，够一个 bot 直接在 PR 上留评论：

```bash
python skills_lint.py ./skills --json | jq -r '
  .skills[] | select(.score < 90) |
  "\(.name): \(.score) - " + ([.findings[] | .rule] | join(", "))'
```

## 它不做什么

- 不评价指令写得好不好。静态工具做不到。
- 不运行 skill。这是 lint，不是评测。
- 不改任何文件。每条提示都是给人看的。

## 已知误报

- **SL010**：有些运行时只靠词汇匹配，不依赖显式触发子句。如果你的环境是这样，skill 也
  正常工作，忽略这条。
- **SL014**：文档示例里的占位符（比如 `[标题](some.field)`）可能被当成路径。不含路径分隔符
  也没有已知扩展名的裸词会被跳过，但特殊占位符仍可能漏网。

## 许可

MIT
