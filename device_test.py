import asyncio
import websockets
import json

SERVER_URL = "ws://1.13.165.120:8000/ws/device/node_001"
HEARTBEAT_INTERVAL = 15
RECONNECT_DELAY = 5


async def send_heartbeat(ws):
    while True:
        await ws.send(json.dumps({
            "type": "heartbeat"
        }))
        print("已发送心跳")
        await asyncio.sleep(HEARTBEAT_INTERVAL)


async def receive_commands(ws):
    while True:
        msg = await ws.recv()
        print("收到服务器/APP命令：", msg)


async def send_demo_alarm(ws):
    await asyncio.sleep(3)
    await ws.send(json.dumps({
        "type": "alarm",
        "event": "fall"
    }))
    print("已发送报警")


async def device_main():
    while True:
        heartbeat_task = None
        recv_task = None
        alarm_task = None

        try:
            print("正在连接服务器...")
            async with websockets.connect(
                SERVER_URL,
                ping_interval=20,
                ping_timeout=20
            ) as ws:
                print("设备已连接服务器")

                await ws.send(json.dumps({
                    "type": "device_login"
                }))
                print("已发送登录消息")

                heartbeat_task = asyncio.create_task(send_heartbeat(ws))
                recv_task = asyncio.create_task(receive_commands(ws))
                alarm_task = asyncio.create_task(send_demo_alarm(ws))

                done, pending = await asyncio.wait(
                    [heartbeat_task, recv_task, alarm_task],
                    return_when=asyncio.FIRST_EXCEPTION
                )

                for task in pending:
                    task.cancel()

                for task in done:
                    exc = task.exception()
                    if exc:
                        raise exc

        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"连接断开，{RECONNECT_DELAY}秒后重连：{e}")
            await asyncio.sleep(RECONNECT_DELAY)
        finally:
            for task in [heartbeat_task, recv_task, alarm_task]:
                if task is not None and not task.done():
                    task.cancel()


if __name__ == "__main__":
    asyncio.run(device_main())