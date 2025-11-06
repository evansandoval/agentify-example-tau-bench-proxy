"""CLI entry point for agentify-example-tau-bench."""

import typer
import asyncio

from src.green_agent import start_green_agent
from src.white_agent import start_white_agent
from src.launcher import launch_evaluation, launch_remote_evaluation
from pydantic_settings import BaseSettings


class TaubenchSettings(BaseSettings):
    role: str = "unspecified"
    host: str = "127.0.0.1"
    agent_port: int = 9000


app = typer.Typer(help="Agentified Tau-Bench - Standardized agent assessment framework")


@app.command()
def green():
    """Start the green agent (assessment manager)."""
    start_green_agent()


@app.command()
def white():
    """Start the white agent (target being tested)."""
    start_white_agent()


@app.command()
def run():
    settings = TaubenchSettings()
    if settings.role == "green":
        start_green_agent(host=settings.host, port=settings.agent_port)
    elif settings.role == "white":
        start_white_agent(host=settings.host, port=settings.agent_port)
    else:
        raise ValueError(f"Unknown role: {settings.role}")
    return


@app.command()
def launch():
    """Launch the complete evaluation workflow."""
    asyncio.run(launch_evaluation())


@app.command()
def launch_remote(green_url: str, white_url: str):
    """Launch the complete evaluation workflow."""
    asyncio.run(launch_remote_evaluation(green_url, white_url))


if __name__ == "__main__":
    app()
