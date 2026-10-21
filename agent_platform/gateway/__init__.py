# -*- coding: utf-8 -*-
"""军团门面包（WO-LEGION-S21）：对外稳定接口的唯一入口。

外部板块（飞书中台等）只 import 本包，不碰军团内部模块：
  from gateway import llm_gateway, inbox_gateway
  from gateway.envelope import validate_envelope
"""
