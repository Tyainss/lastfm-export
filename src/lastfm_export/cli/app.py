import typer
from dotenv import load_dotenv

from lastfm_export.cli.commands_enrich import enrich_app
from lastfm_export.cli.commands_scrobbles import scrobbles_app

load_dotenv()  # Load environment variables from .env file, if it exists
app = typer.Typer(no_args_is_help=True)
app.add_typer(scrobbles_app, name="scrobbles")
app.add_typer(enrich_app, name="enrich")


def main() -> None:
    app()