# 翻译任务说明（英语听力视频字幕）

你的任务：把 `work/batch_NN_in.json` 里的英文内容翻译成**自然、口语化的简体中文**，
写入 `work/translations/batch_NN.json`。

## 输入格式

```json
[
  {"row": 22, "level": "A1", "title_en": "What do you do on the weekend?",
   "paras_en": ["Hello, my name is ...", "I usually ...", "What about you?"]}
]
```

- `paras_en` 是按段落切好的英文，一个元素就是一段。
- 这些是英语学习网站的口语访谈录音稿，说话人来自世界各地，语气都很随意、生活化。

## 输出格式（严格遵守）

```json
[
  {"row": 22, "title_cn": "你周末都做什么？",
   "paras_cn": ["你好，我叫……", "我通常……", "你呢？"]}
]
```

- 每条必须带 `row`（与输入一致）、`title_cn`、`paras_cn` 三个字段。
- **`paras_cn` 的长度必须与 `paras_en` 完全相等**，一一对应，顺序不能变、不能合并、不能拆分。
  输入 3 段就必须输出 3 段。
- 只输出 JSON 数组本身，不要加解释、注释、markdown 代码围栏之外的任何文字。

## 翻译要求

1. **意译优先，符合中文说话习惯**，不要逐字硬译。
   - `We don't have a dog.` → `我们没有养狗。`（不是"我们没有狗"）
   - `I don't belong to a gym.` → `我没有办健身卡。`（不是"我不属于健身房"）
   - `I walk my dog to the park.` → `我遛狗会去公园。`
   - `My grandfather keeps many pigs and chickens.` → `我爷爷养了很多猪和鸡。`
   - `It is not as hot as summer.` → `不像夏天那么热。`
   - `I like to race, so I need to practice.` → `我喜欢比赛，所以得练。`
   - `She is a night person.` → `我是夜猫子型的人。`
   - `The cafeteria has many options.` → `食堂的选择挺多。`
2. **信息完整**：不遗漏、不添加原文没有的内容。
3. **保留口语感**：可以有"嗯""其实""挺""挺不错的"这类自然口语词，但不要过度。
   原文有 `Well,` / `So,` / `You know,` 这类口头语，译成中文里自然的对应表达即可
   （"嗯""所以""你知道的"），不要生硬删掉导致语气失真。
4. **数字用阿拉伯数字**：`45 years old` → `45 岁`，`three parks` → `3 个公园`。
5. **人名、地名用常见中文译名**：`Cecilia` → `塞西莉亚`，`Armenia` → `亚美尼亚`，
   `H&M` 这类品牌保留原文。同一批里同一个人名要译法一致。
6. **标点用中文全角**：，。？！、；：""。
7. **标题 `title_cn`**：
   - 如果 `title_en` 形如 `Intermediate English - 43 - What do you usually eat for dinner?`，
     译成 `中级英语 - 43 - 你晚餐通常吃什么？`（编号、连字符格式保留，难度名对应翻译：
     `Beginner English` → `初级英语`，`Intermediate English` → `中级英语`，
     `High-Intermediate English` → `中高级英语`）。
   - 否则直接译成自然的中文问句/短语。
8. 不要输出拼音、英文原文、括号注释。

## 完成后

1. 用 `write` 工具把 JSON 写到 `work/translations/batch_NN.json`（NN 与输入文件一致）。
2. 写完后自查一遍：条数是否与输入相同、每条的 `paras_cn` 长度是否与 `paras_en` 相等。
3. 最后回复一行确认，例如：`batch_03 完成：12 行，段落数全部匹配`。
