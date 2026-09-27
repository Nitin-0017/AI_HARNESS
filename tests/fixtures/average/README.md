# Intentionally failing fixture

`average([])` is broken. The harness tests copy this directory into a disposable
standalone target Git repository. These files are never used as the harness itself.
