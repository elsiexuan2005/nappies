"""Development MQTT broker (pure Python, amqtt) for running without Docker/Mosquitto.
In production the edge hub runs Mosquitto (see deploy/docker-compose.edge.yml).

  MQTT_PORT=1883 python services/edge/dev_broker.py
"""
import asyncio
import os

from amqtt.broker import Broker


async def main():
    port = int(os.environ.get("MQTT_PORT", "1883"))
    bind = os.environ.get("MQTT_BIND", "127.0.0.1")
    b = Broker({"listeners": {"default": {"type": "tcp", "bind": f"{bind}:{port}"}}})
    await b.start()
    print(f"[broker] MQTT on {bind}:{port}", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
