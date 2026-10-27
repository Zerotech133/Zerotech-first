# -*- coding: utf-8 -*-
"""monday_checkpoint._ran_today 单测（K-ORG 审计 P1-1 修复回归）。

PowerShell Get-ScheduledTaskInfo LastRunTime 本机为斜杠格式
（"2026/10/17 23:27"），修复前 startswith(ISO) 恒 False 致门 4 fail-open。
"""

import sys
import os
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import monday_checkpoint as mc  # noqa: E402

TODAY = date.today()


def _fmt(y, m, d, sep, with_time=True):
    s = "%d%s%d%s%d" % (y, sep, m, sep, d)
    return "%s 17:25" % s if with_time else s


def test_slash_format_today():
    assert mc._ran_today(_fmt(TODAY.year, TODAY.month, TODAY.day, "/"))


def test_slash_format_yesterday_not_today():
    assert not mc._ran_today(_fmt(2026, 10, 17, "/"))


def test_iso_format_today():
    assert mc._ran_today(_fmt(TODAY.year, TODAY.month, TODAY.day, "-"))


def test_iso_t_separator_today():
    s = "%d-%02d-%02dT17:25:00" % (TODAY.year, TODAY.month, TODAY.day)
    assert mc._ran_today(s)


def test_empty_and_garbage_false():
    assert mc._ran_today("") is False
    assert mc._ran_today("   ") is False
    assert mc._ran_today("never") is False
    assert mc._ran_today("2026/13/45 08:00") is False
