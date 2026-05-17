"""
Generate a bcrypt password hash for the APP_PASSWORD_HASH env var.

Usage:
    python scripts/hash_password.py
"""
import getpass
import bcrypt


def main():
    print("=" * 60)
    print("GSTR-1 Generator — password hash generator")
    print("=" * 60)
    print()
    print("This generates a bcrypt hash of your password.")
    print("Set this hash as the APP_PASSWORD_HASH env var on Render.")
    print()

    while True:
        p1 = getpass.getpass("Enter password (min 10 chars): ")
        if len(p1) < 10:
            print("  Too short — use at least 10 characters.\n")
            continue
        p2 = getpass.getpass("Confirm password: ")
        if p1 != p2:
            print("  Passwords don't match. Try again.\n")
            continue
        break

    hashed = bcrypt.hashpw(p1.encode("utf-8"), bcrypt.gensalt(rounds=12))
    print()
    print("=" * 60)
    print("Copy this hash and paste it into Render's APP_PASSWORD_HASH env var:")
    print()
    print(hashed.decode("utf-8"))
    print()
    print("=" * 60)
    print("⚠ Do NOT commit this hash to git. Set it only in Render's dashboard.")
    print("⚠ Do NOT share this hash.")


if __name__ == "__main__":
    main()
