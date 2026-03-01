import typer

from lastfm_export.cli.commands_enrich import enrich_app
from lastfm_export.cli.commands_scrobbles import scrobbles_app

app = typer.Typer(no_args_is_help=True)
app.add_typer(scrobbles_app, name="scrobbles")
app.add_typer(enrich_app, name="enrich")


def main() -> None:
    app()