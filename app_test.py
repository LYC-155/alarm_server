import asyncio
import websockets
import json
from typing import Union

SERVER_URL = "ws://1.13.165.120:8000/ws/app/node_001"
RECONNECT_DELAY = 5


async def handle_message(raw_msg: Union[str, bytes]):
    if isinstance(raw_msg, bytes):
        raw_msg = raw_msg.decode("utf-8")

    print("收到消息：", raw_msg)

    try:
        msg = json.loads(raw_msg)
    except json.JSONDecodeError:
        print("收到非JSON消息")
        return

    msg_type = msg.get("type")

    if msg_type == "system":
        online = msg.get("device_online")
        text = msg.get("message")
        print(f"[系统消息] 设备在线={online}, 内容={text}")

    elif msg_type == "device_login":
        print("[设备消息] 设备已登录")

    elif msg_type == "alarm":
        event = msg.get("event")
        print(f"[报警消息] event={event}")
        if event == "fall":
            print(">>> 检测到跌倒报警 <<<")

    else:
        print(f"[其他消息] type={msg_type}")


async def app_main():
    while True:
        try:
            print("正在连接服务器...")
            async with websockets.connect(
                SERVER_URL,
                ping_interval=20,
                ping_timeout=20
            ) as ws:
                print("App已连接服务器，开始监听报警")

                while True:
                    raw_msg = await ws.recv()
                    await handle_message(raw_msg)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"App连接断开，{RECONNECT_DELAY}秒后重连：{e}")
            await asyncio.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    asyncio.run(app_main())