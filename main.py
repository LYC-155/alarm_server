from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, JSONResponse
from typing import Dict, List
import json
from datetime import datetime
import asyncio
import time
import base64
from contextlib import asynccontextmanager

# =========================
# 全局变量区
# =========================

device_connections: Dict[str, WebSocket] = {}
app_connections: Dict[str, List[WebSocket]] = {}
device_last_seen: Dict[str, float] = {}

# 保存每个设备最新一帧 JPEG（二进制）
latest_frames: Dict[str, bytes] = {}

HEARTBEAT_TIMEOUT = 40


# =========================
# 工具函数区
# =========================

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def notify_apps(device_id: str, payload: dict):
    if device_id not in app_connections:
        return

    dead_clients = []

    for client in app_connections[device_id]:
        try:
            await client.send_text(json.dumps(payload, ensure_ascii=False))
        except Exception:
            dead_clients.append(client)

    for dead in dead_clients:
        if dead in app_connections.get(device_id, []):
            app_connections[device_id].remove(dead)

    if device_id in app_connections and not app_connections[device_id]:
        del app_connections[device_id]


async def broadcast_device_status(device_id: str, online: bool, message: str):
    payload = {
        "type": "system",
        "device_id": device_id,
        "device_online": online,
        "message": message,
        "server_time": now_str()
    }
    await notify_apps(device_id, payload)


async def send_command_to_device(device_id: str, cmd: str, extra: dict | None = None):
    if device_id not in device_connections:
        return False

    payload = {"cmd": cmd}
    if extra:
        payload.update(extra)

    try:
        await device_connections[device_id].send_text(
            json.dumps(payload, ensure_ascii=False)
        )
        return True
    except Exception as e:
        print(f"[SEND COMMAND ERROR] {device_id}: {e}")
        return False


async def heartbeat_checker():
    while True:
        now = time.time()
        timeout_devices = []

        for device_id, last_seen in list(device_last_seen.items()):
            if now - last_seen > HEARTBEAT_TIMEOUT:
                timeout_devices.append(device_id)

        for device_id in timeout_devices:
            if device_id in device_connections:
                try:
                    await device_connections[device_id].close()
                except Exception:
                    pass
                if device_id in device_connections:
                    del device_connections[device_id]

            if device_id in device_last_seen:
                del device_last_seen[device_id]

            if device_id in latest_frames:
                del latest_frames[device_id]

            print(f"[HEARTBEAT TIMEOUT] {device_id}")

            await broadcast_device_status(
                device_id=device_id,
                online=False,
                message="device offline (heartbeat timeout)"
            )

        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(heartbeat_checker())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(lifespan=lifespan)


# =========================
# HTTP 接口区
# =========================

@app.get("/")
def root():
    return {"msg": "server is running"}


@app.get("/ping")
def ping():
    return {"status": "ok"}


@app.get("/status")
def status():
    now = time.time()
    online_devices = []

    for device_id, last_seen in device_last_seen.items():
        if now - last_seen <= HEARTBEAT_TIMEOUT:
            online_devices.append(device_id)

    return {
        "online_devices": online_devices,
        "app_subscribers": {k: len(v) for k, v in app_connections.items()},
        "device_last_seen": {
            k: datetime.fromtimestamp(v).strftime("%Y-%m-%d %H:%M:%S")
            for k, v in device_last_seen.items()
        },
        "has_video_frame": {k: True for k in latest_frames.keys()}
    }


@app.get("/snapshot/{device_id}")
def snapshot(device_id: str):
    frame = latest_frames.get(device_id)
    if frame is None:
        return JSONResponse(
            status_code=404,
            content={"error": "no frame available"}
        )
    return StreamingResponse(
        iter([frame]),
        media_type="image/jpeg"
    )


@app.get("/video_feed/{device_id}")
async def video_feed(device_id: str):
    async def generate():
        while True:
            frame = latest_frames.get(device_id)
            if frame is not None:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                )
            await asyncio.sleep(0.15)  # 大约 6~7 FPS 显示

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


# =========================
# 设备 WebSocket 接口
# =========================

@app.websocket("/ws/device/{device_id}")
async def device_ws(websocket: WebSocket, device_id: str):
    await websocket.accept()

    old_ws = device_connections.get(device_id)
    if old_ws and old_ws != websocket:
        try:
            await old_ws.close()
        except Exception:
            pass

    device_connections[device_id] = websocket
    device_last_seen[device_id] = time.time()

    print(f"[DEVICE CONNECTED] {device_id}")

    await broadcast_device_status(
        device_id=device_id,
        online=True,
        message="device connected"
    )

    try:
        while True:
            data = await websocket.receive_text()
            device_last_seen[device_id] = time.time()
            print(f"[DEVICE MESSAGE] {device_id}: {data[:120]}{'...' if len(data) > 120 else ''}")

            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                msg = {"type": "raw", "content": data}

            msg_type = msg.get("type")

            if msg_type == "heartbeat":
                continue

            if msg_type == "video_frame":
                image_b64 = msg.get("image", "")
                if image_b64:
                    try:
                        latest_frames[device_id] = base64.b64decode(image_b64)
                    except Exception as e:
                        print(f"[VIDEO FRAME DECODE ERROR] {device_id}: {e}")
                continue

            msg["device_id"] = device_id
            msg["server_time"] = now_str()

            await notify_apps(device_id, msg)

    except WebSocketDisconnect:
        print(f"[DEVICE DISCONNECTED] {device_id}")
    except Exception as e:
        print(f"[DEVICE ERROR] {device_id}: {e}")
    finally:
        if device_connections.get(device_id) == websocket:
            del device_connections[device_id]

        if device_id in device_last_seen:
            del device_last_seen[device_id]

        if device_id in latest_frames:
            del latest_frames[device_id]

        await broadcast_device_status(
            device_id=device_id,
            online=False,
            message="device disconnected"
        )


# =========================
# App WebSocket 接口
# =========================

@app.websocket("/ws/app/{device_id}")
async def app_ws(websocket: WebSocket, device_id: str):
    await websocket.accept()

    if device_id not in app_connections:
        app_connections[device_id] = []

    app_connections[device_id].append(websocket)

    print(f"[APP CONNECTED] subscribe device={device_id}")

    now = time.time()
    online = (
        device_id in device_last_seen and
        now - device_last_seen[device_id] <= HEARTBEAT_TIMEOUT
    )

    await websocket.send_text(json.dumps({
        "type": "system",
        "device_id": device_id,
        "device_online": online,
        "message": "app connected",
        "server_time": now_str()
    }, ensure_ascii=False))

    try:
        while True:
            data = await websocket.receive_text()
            print(f"[APP MESSAGE] {device_id}: {data}")

            try:
                msg = json.loads(data)
                msg_type = msg.get("type", "")

                if msg_type == "heartbeat":
                    await websocket.send_text(json.dumps({
                        "type": "system",
                        "device_id": device_id,
                        "device_online": device_id in device_connections,
                        "message": "heartbeat ok",
                        "server_time": now_str()
                    }, ensure_ascii=False))
                    continue
            except json.JSONDecodeError:
                msg = {"type": "raw", "content": data}

            cmd = msg.get("cmd", "")

            if cmd in ["start_stream", "stop_stream"]:
                ok = await send_command_to_device(device_id, cmd)
                await websocket.send_text(json.dumps({
                    "type": "system",
                    "device_id": device_id,
                    "device_online": device_id in device_connections,
                    "message": f"{cmd} {'sent' if ok else 'failed'}",
                    "server_time": now_str()
                }, ensure_ascii=False))
                continue

            if device_id in device_connections:
                try:
                    await device_connections[device_id].send_text(data)
                except Exception as e:
                    print(f"[FORWARD TO DEVICE ERROR] {device_id}: {e}")
            else:
                await websocket.send_text(json.dumps({
                    "type": "system",
                    "device_id": device_id,
                    "device_online": False,
                    "message": "device is offline, command not delivered",
                    "server_time": now_str()
                }, ensure_ascii=False))

    except WebSocketDisconnect:
        print(f"[APP DISCONNECTED] device={device_id}")
    except Exception as e:
        print(f"[APP ERROR] device={device_id}: {e}")
    finally:
        if device_id in app_connections and websocket in app_connections[device_id]:
            app_connections[device_id].remove(websocket)

        if device_id in app_connections and not app_connections[device_id]:
            del app_connections[device_id]