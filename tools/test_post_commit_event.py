"""post-commit 事件适配器测试。"""

from post_commit_event import extract_task_id


def test_extract_task_id_from_merge_subject():
    assert extract_task_id("merge task/K-STOCK-223: distillation") == "K-STOCK-223"


def test_extract_task_id_is_empty_without_ticket():
    assert extract_task_id("chore: refresh local metadata") == ""
