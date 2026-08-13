{ pkgs }: {
  deps = [
    pkgs.python311
    pkgs.python311Packages.pip
    pkgs.postgresql
    # NO CHROMIUM HERE, DELIBERATELY. The 513 browser tests need one and
    # playwright's own cannot start on this container — `ldd` reports 26
    # missing libraries and `playwright install-deps` needs apt, which Nix does
    # not have. `pkgs.chromium` was added to solve that and did not take
    # effect, so it is gone rather than left costing ~150MB of image for
    # nothing.
    #
    # THE BROWSER TESTS RUN IN CI. They pass on any ordinary Linux host; the
    # obstacle is this environment, not the tests. `conftest._chrome_path()`
    # honours CHROME_BIN, so a CI job only has to point it at a chromium.
  ];
}
