"""Command line entry point."""

import click

from notekeeper.core import add_note, find_notes, list_notes


@click.group()
def cli() -> None:
    """Keep notes in a JSON file."""


@cli.command()
@click.argument("text")
def add(text: str) -> None:
    """Add a note."""
    note = add_note(text)
    click.echo(f"added note {note['id']}")


@cli.command("list")
def list_command() -> None:
    """Print every note."""
    for note in list_notes():
        click.echo(f"{note['id']}: {note['text']}")


@cli.command()
@click.argument("word")
def search(word: str) -> None:
    """Print the notes that contain a word."""
    for note in find_notes(word):
        click.echo(f"{note['id']}: {note['text']}")


if __name__ == "__main__":
    cli()
