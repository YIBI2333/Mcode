# -*- coding: utf-8 -*-
"""流式拆句器表格处理测试。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agent.pipeline import StreamingSentenceSplitter


def _split_stream(text, chunk_size=5):
    """模拟流式传输：按 chunk 喂入 + flush，返回所有句子。"""
    s = StreamingSentenceSplitter()
    out = []
    for i in range(0, len(text), chunk_size):
        for sent in s.feed(text[i:i + chunk_size]):
            out.append(sent)
    for sent in s.flush():
        out.append(sent)
    return out


def test_table_kept_intact():
    text = (
        "这是一张表格：\n"
        "| 动作 | 反应 | 尾巴 | 好感度 |\n"
        "| --- | --- | --- | --- |\n"
        "| 说早安 | 精神抖擞 | 竖起来 | +2 |\n"
        "| 摸头 | 眯眼蹭手心 | 卷成问号 | +3 |\n"
        "怎么样主人～"
    )
    sents = _split_stream(text)
    print("SENTS:", sents)
    # 表格应作为一个整体句子（带 [MD] 前缀），不被拆成多行
    table = [s for s in sents if '|' in s]
    assert len(table) == 1, f"表格被拆散: {table}"
    assert table[0].startswith('[MD]'), f"表格未加 [MD] 前缀: {table[0]!r}"
    # 表格内应包含所有数据行
    assert '说早安' in table[0]
    assert '摸头' in table[0]


def test_table_then_text():
    text = "| a | b |\n|---|---|\n| 1 | 2 |\n后面还有话。"
    sents = _split_stream(text)
    print("SENTS:", sents)
    table = [s for s in sents if '|' in s]
    assert len(table) == 1, f"表格被拆散: {table}"
    assert '后面还有话' in sents[-1]


def test_trailing_table_gets_md_prefix():
    # 流以表格结尾（无后续文本）
    text = "| a | b |\n|---|---|\n| 1 | 2 |"
    sents = _split_stream(text)
    print("SENTS:", sents)
    assert len(sents) == 1
    assert sents[0].startswith('[MD]'), f"结尾表格未加 [MD]: {sents[0]!r}"


def test_no_table_normal_split():
    text = "第一句。第二句！第三句？"
    sents = _split_stream(text)
    print("SENTS:", sents)
    assert len(sents) == 3


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
