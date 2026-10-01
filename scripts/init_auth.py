"""Create private credentials interactively; never print passwords or hashes."""

import argparse
import getpass
import os
from pathlib import Path

import bcrypt
import yaml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    args = parser.parse_args()
    if (
        args.config.exists()
        and "REPLACE_WITH_BCRYPT_HASH" not in args.config.read_text()
    ):
        parser.error(
            "Configuration existante : choisissez un autre fichier avec --config."
        )
    username = input("Identifiant : ").strip()
    password = getpass.getpass("Nouveau mot de passe : ")
    if not username or not password or len(password.encode()) > 72:
        parser.error(
            "Identifiant requis ; mot de passe requis, maximum 72 octets UTF-8."
        )
    if password != getpass.getpass("Confirmez le mot de passe : "):
        parser.error("Les mots de passe diffèrent.")
    config = {
        "credentials": {
            "usernames": {
                username: {
                    "name": username,
                    "password": bcrypt.hashpw(
                        password.encode(), bcrypt.gensalt()
                    ).decode(),
                }
            }
        },
        "cookie": {"name": "docmind_session", "expiry_days": 1},
    }
    fd = os.open(args.config, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as output:
        yaml.safe_dump(config, output, allow_unicode=True)
    print("Configuration privée créée. Vous pouvez démarrer DocMind.")


if __name__ == "__main__":
    main()
