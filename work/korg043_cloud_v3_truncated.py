# 在 build_prompt 函数中，对 note 进行长度截断和破栏替换操作

def build_prompt(sha, diff, note=None):
    # 截断 note 到至多 2048 字符，并添加省略号后缀
    note = note[:2048] + "..." if note else ""
    # 替换 note 中的 '>>>>' 为 '>'*3
    note = note.replace(">>>>", ">"*3)
    note_blk = ("\n【提交方附证（可复现现值核验）】\n<<<<\n%s\n>>>>\n" % note) if note else ""
    return (
        f"你是 QA1 质检复核员，请复核以下这笔提交。\n"
        f"提交 sha: {sha}\n\n"
        f"diff（截前 {DIFF_MAX_CHARS} 字，完整 diff 可用 git show {sha} 复现）:\n"
        f"<<<<\n{diff}\n>>>>\n{note_blk}\n"
        "请逐项回答：\n"
        "1) 改动与提交消息是否相符；\n"
        "2) 有无逻辑/边界隐患；\n"
        "3) 结论：必须明确给出「通过」或「退回+理由」。\n"
        "【diff 判读纪律（K-ORG-042 判例）】diff 中以 - 开头的行是被「移除的旧内容」"
        "而非提交后的现状——判定「某引用仍存在」不能以 - 行为据；"
        "负面断言（如「已清零/已全删」）以【提交方附证】的现值计数为准，"
        "无附证且 diff 截断不足以判断时，结论给「退回+理由：需附证重提」。\n"
        "输出保持简洁，结论放在最后一行。"
    )