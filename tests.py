import asyncio
from collections.abc import AsyncGenerator
import socket

from aiohttp import client, web
from prometheus_client.parser import text_string_to_metric_families
import pytest
import pytest_asyncio

import hyperlane_network_exporter


def find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return int(s.getsockname()[1])


@pytest_asyncio.fixture
async def metrics_server(
    rpc: tuple[str, hyperlane_network_exporter.SupportedNetworks],
) -> AsyncGenerator[dict[str, str], None]:
    loop = asyncio.get_event_loop()
    ethereum_rpc, network = rpc
    exporter = hyperlane_network_exporter.HyperlaneContractExporter(
        ethereum_rpc, loop=loop
    )
    port = find_free_port()
    app = hyperlane_network_exporter.get_application(exporter)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", port)
    await site.start()
    yield {
        "server": f"http://localhost:{port}",
        "network": network,
    }
    await runner.shutdown()
    await site.stop()
    hyperlane_network_exporter.hyperlane_contract_latest_checkpoint.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rpc",
    [
        (
            "https://ethereum-rpc.publicnode.com",
            hyperlane_network_exporter.SupportedNetworks.MAINNET,
        ),
        (
            "https://ethereum-holesky-rpc.publicnode.com",
            hyperlane_network_exporter.SupportedNetworks.HOLESKY,
        ),
    ],
)
async def test_metrics(metrics_server: dict[str, str]) -> None:
    async with client.ClientSession() as session:
        matched = False
        server_url = metrics_server["server"]
        response = await session.get(f"{server_url}/metrics")
        assert response.status == 200
        for metric in text_string_to_metric_families(await response.text()):
            if metric.name.startswith("hyperlane"):
                assert metric.name == "hyperlane_contract_latest_checkpoint"
                sample = metric.samples[0]
                assert sample.labels["network"] == str(metrics_server["network"])
                matched = True

    assert matched
