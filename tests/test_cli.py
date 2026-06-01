import importlib.metadata


def test_console_script_points_to_cli_main():
    scripts = importlib.metadata.entry_points(group="console_scripts")
    colab_cli = [script for script in scripts if script.name == "colab-cli"]

    assert len(colab_cli) == 1
    assert colab_cli[0].value == "colab_cli.cli:main"
