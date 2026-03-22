import time
import sys

def simple_forward_timer():
    """简单正向计时器（只显示秒）"""
    seconds = 0
    try:
        while True:
            print(f"\r已运行：{seconds}秒", end='', flush=True)
            time.sleep(1)
            seconds += 1
    except KeyboardInterrupt:
        print(f"\n总共运行了 {seconds} 秒")

simple_forward_timer()