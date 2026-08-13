{ pkgs }: {
  deps = [
    pkgs.python311
    pkgs.python311Packages.pip
    pkgs.postgresql
    # FOR THE 513 DASHBOARD TESTS, and for nothing the app itself does.
    #
    # playwright ships its own chromium, and on this Nix container that binary
    # cannot start: `ldd` reports 26 missing libraries (libnss3, libgbm,
    # libX11, libatk...). `playwright install-deps` supplies them with apt,
    # which Nix does not have. So the browser tests collected and every one of
    # them skipped, leaving the dashboard — the part other people use — as the
    # least covered thing in the project.
    #
    # Nix's chromium comes with its libraries resolved. Point the harness at it:
    #
    #     CHROME_BIN=$(which chromium) python -m pytest tests/ -q
    #
    # IT COSTS SPACE IN THE IMAGE, roughly 150MB, for something only the tests
    # use. If that matters more than running these tests here, delete this line
    # and run them in CI instead — they pass on any ordinary Linux host.
    pkgs.chromium
  ];
}
