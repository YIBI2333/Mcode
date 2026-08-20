"""独立测试 TTS — 不依赖任何项目模块，直接测试 edge_tts 是否可用"""
import asyncio
import sys
import os

async def test():
    text = "你好呀，今天天气真不错"
    print(f"测试文本: {text}")

    # 1. 测试 edge_tts 库本身
    try:
        import edge_tts
        print(f"edge_tts 版本: {edge_tts.__version__ if hasattr(edge_tts, '__version__') else 'unknown'}")
    except ImportError as e:
        print(f"ERROR: edge_tts 未安装: {e}")
        return

    # 2. 测试 TTS 合成
    cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tts_cache")
    os.makedirs(cache_dir, exist_ok=True)
    mp3_path = os.path.join(cache_dir, "test_tts.mp3")

    print(f"缓存目录: {cache_dir}")
    print("开始合成...")

    try:
        comm = edge_tts.Communicate(text, "zh-CN-XiaoyiNeural", rate="-10%")
        await asyncio.wait_for(comm.save(mp3_path), timeout=15.0)
        size = os.path.getsize(mp3_path)
        print(f"OK! 文件已生成: {mp3_path} ({size} bytes)")
    except asyncio.TimeoutError:
        print("ERROR: 合成超时 (15s) — 网络无法连接 Edge TTS")
    except Exception as e:
        print(f"ERROR: 合成失败: {type(e).__name__}: {e}")

    # 3. 测试流式获取音素
    print("\n获取音素...")
    try:
        comm2 = edge_tts.Communicate(text, "zh-CN-XiaoyiNeural", rate="-10%")
        word_count = 0
        async for chunk in comm2.stream():
            if chunk["type"] == "WordBoundary":
                word_count += 1
        print(f"OK! 获取到 {word_count} 个字边界")
    except Exception as e:
        print(f"ERROR: 音素获取失败: {type(e).__name__}: {e}")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(test())
